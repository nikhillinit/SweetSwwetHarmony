import logging
import requests
import io
import socket
import ipaddress
from contextlib import closing
from urllib.parse import urlparse, urljoin
from typing import Dict, Any, Set, Tuple

logger = logging.getLogger(__name__)


class YouTubeFetcher:
    ALLOWED_DOMAINS: Set[str] = {
        "www.youtube.com",
        "youtube.com",
        "m.youtube.com",
        "youtu.be",
    }

    ALLOWED_SUBTITLE_DOMAINS: Set[str] = {
        "www.youtube.com",
        "youtube.com",
        "www.googlevideo.com",
        "googlevideo.com",
    }

    BLOCKED_PATHS = {
        "/redirect",
        "/signin",
        "/accounts",
        "/get_video_info",
    }
    
    MAX_REDIRECTS = 2

    def __init__(self):
        try:
            import yt_dlp
            self.ydl = yt_dlp
        except ImportError:
            raise ImportError("Install yt-dlp: pip install yt-dlp")

        try:
            import webvtt
            self.webvtt = webvtt
        except ImportError:
            raise ImportError("Install webvtt-py: pip install webvtt-py")

    def _validate_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)

            if parsed.scheme not in ("http", "https"):
                return False

            host = (parsed.hostname or "").lower()

            if host not in self.ALLOWED_DOMAINS:
                return False

            if parsed.path in self.BLOCKED_PATHS:
                logger.warning(f"Blocked redirect endpoint: {parsed.path}")
                return False

            return True

        except Exception:
            return False

    @staticmethod
    def _host_resolves_to_public_ips(host: str) -> bool:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        except Exception:
            return False

        ips = {info[4][0] for info in infos if info and len(info) >= 5 and info[4]}
        if not ips:
            return False

        for ip in ips:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                return False

            if not addr.is_global:
                logger.warning(f"Blocked non-public IP for {host}: {addr}")
                return False

        return True

    def _is_safe_subtitle_domain(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()

            # Reject non-standard ports (only allow None or 443 for HTTPS)
            if parsed.port is not None and parsed.port != 443:
                logger.warning(f"Blocked non-standard port for subtitle URL: {parsed.port}")
                return False

            if host in self.ALLOWED_SUBTITLE_DOMAINS:
                return self._host_resolves_to_public_ips(host)

            if host.endswith(".googlevideo.com") or host.endswith(".youtube.com"):
                if host.count(".") > 1:
                    return self._host_resolves_to_public_ips(host)

            return False

        except Exception:
            return False

    def _fetch_with_redirect_control(self, url: str, max_redirects: int = 2) -> Tuple[bytes, int]:
        """
        Fetch URL with controlled redirect following.
        Returns (content_bytes, final_status_code).
        
        Validates each redirect target against allowlist before following.
        Uses context manager to ensure connection cleanup.
        Raises requests.exceptions.RequestException on error.
        """
        current_url = url
        visited_urls = set()
        timeout = (10, 30)  # (connect_timeout, read_timeout)
        
        for redirect_count in range(max_redirects + 1):
            # Detect redirect loops
            if current_url in visited_urls:
                raise requests.exceptions.TooManyRedirects("Redirect loop detected")
            visited_urls.add(current_url)
            
            # Use closing() to guarantee cleanup even on exceptions during streaming
            with closing(requests.get(
                current_url,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0 (compatible; InternalTool/1.0)"},
            )) as response:
                
                # If not a redirect, consume content and return
                if response.status_code < 300 or response.status_code >= 400:
                    # Check content length header
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) > 1_000_000:
                                logger.warning("Subtitle file too large (Content-Length), will truncate")
                        except ValueError:
                            pass
                    
                    # Stream content with size limit
                    content = bytearray()
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        content.extend(chunk)
                        if len(content) > 1_000_000:
                            logger.warning("Subtitle file too large, truncating at 1MB")
                            break
                    
                    return bytes(content), response.status_code
                
                # Handle redirect
                if redirect_count >= max_redirects:
                    raise requests.exceptions.TooManyRedirects(
                        f"Exceeded {max_redirects} redirects"
                    )
                
                # Get redirect location
                location = response.headers.get('location')
                if not location:
                    raise requests.exceptions.RequestException(
                        "Redirect response missing Location header"
                    )
                
                # Resolve relative URLs
                current_url = urljoin(current_url, location)
                
                # Validate the redirect target
                parsed = urlparse(current_url)
                if parsed.scheme != "https":
                    raise requests.exceptions.RequestException(
                        f"Redirect to non-HTTPS URL blocked: {current_url}"
                    )
                
                if not self._is_safe_subtitle_domain(current_url):
                    raise requests.exceptions.RequestException(
                        f"Redirect to non-allowed domain blocked: {parsed.hostname}"
                    )
                
                logger.debug(f"Following redirect {redirect_count + 1}/{max_redirects} to: {current_url}")
                # Continue loop to fetch the new URL
        
        # Should never reach here due to loop structure, but for type safety:
        raise requests.exceptions.TooManyRedirects(f"Exceeded {max_redirects} redirects")

    def fetch_transcript(self, url: str, max_chars: int = 20000) -> Dict[str, Any]:
        if not self._validate_url(url):
            return {"success": False, "error": "URL not allowed. Only YouTube domains permitted."}

        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "quiet": True,
            "no_warnings": True,
        }

        try:
            with self.ydl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                subtitle_url = None
                subs = info.get("subtitles") or info.get("automatic_captions")

                if subs:
                    lang = next((k for k in subs.keys() if k.startswith("en")), None)
                    if lang:
                        for fmt in subs[lang]:
                            if fmt.get("ext") == "vtt" and fmt.get("url"):
                                subtitle_url = fmt["url"]
                                break

                if not subtitle_url:
                    return {"success": False, "error": "No English subtitles available"}

                if not self._is_safe_subtitle_domain(subtitle_url):
                    return {"success": False, "error": "Security check failed: Invalid subtitle source"}

                if not subtitle_url.startswith("https://"):
                    return {"success": False, "error": "Security check failed: HTTPS required"}

                # Fetch with controlled redirect handling
                try:
                    content_bytes, status_code = self._fetch_with_redirect_control(
                        subtitle_url, 
                        max_redirects=self.MAX_REDIRECTS
                    )
                    
                    if status_code >= 400:
                        return {"success": False, "error": f"HTTP error {status_code} fetching subtitles"}

                except requests.exceptions.TooManyRedirects as e:
                    return {"success": False, "error": f"Redirect loop detected: {e}"}
                except requests.exceptions.RequestException as e:
                    return {"success": False, "error": f"Failed to fetch subtitles: {e}"}

                try:
                    vtt_text = content_bytes.decode("utf-8", errors="ignore")
                    buffer = io.StringIO(vtt_text)
                    captions = self.webvtt.read_buffer(buffer)

                    lines = []
                    for caption in captions:
                        line = caption.text.strip().replace("\n", " ")
                        if line:
                            lines.append(line)

                    transcript = " ".join(lines)

                    truncated = False
                    if len(transcript) > max_chars:
                        snippet = transcript[:max_chars]
                        cut_point = max(snippet.rfind("."), snippet.rfind("!"), snippet.rfind("?"))
                        if cut_point > max_chars * 0.8:
                            transcript = transcript[: cut_point + 1]
                        else:
                            cut_point = snippet.rfind(" ")
                            transcript = transcript[:cut_point] if cut_point > 0 else snippet
                        truncated = True

                    return {
                        "success": True,
                        "title": info.get("title", "Unknown"),
                        "duration": info.get("duration", 0),
                        "transcript": transcript,
                        "truncated": truncated,
                        "char_count": len(transcript),
                    }

                except Exception as e:
                    return {"success": False, "error": f"Failed to parse subtitles: {e}"}

        except self.ydl.utils.DownloadError as e:
            return {"success": False, "error": f"YouTube error: {e}"}
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {"success": False, "error": f"Unexpected error: {e}"}


def test_youtube_fetcher():
    fetcher = YouTubeFetcher()
    test_url = "https://www.youtube.com/watch?v=S9HdPi9Ikhk"

    result = fetcher.fetch_transcript(test_url)

    if result.get("success"):
        print(f"✓ Success: {result['title']}")
        print(f"  Duration: {result['duration']}s")
        print(f"  Transcript length: {result['char_count']} chars")
        if result.get("truncated"):
            print("  Note: Transcript was truncated")
        print(f"  Preview: {result['transcript'][:200]}...")
    else:
        print(f"✗ Failed: {result['error']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_youtube_fetcher()
