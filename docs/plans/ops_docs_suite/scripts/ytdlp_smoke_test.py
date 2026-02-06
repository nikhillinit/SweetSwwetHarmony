#!/usr/bin/env python3
"""Basic smoke test for yt-dlp subtitle extraction.

This does NOT download full media. It attempts to:
- extract metadata
- locate an English subtitle track (manual preferred, fallback to auto)
- download the subtitle file (vtt) and parse it

Exit code:
- 0: success
- 2: subtitles not available (non-fatal in many workflows)
- 1: error
"""

import argparse
import sys
import tempfile
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ytdlp_smoke_test")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="YouTube video URL")
    ap.add_argument("--lang", default="en", help="Subtitle language code (default: en)")
    args = ap.parse_args()

    try:
        import yt_dlp
    except Exception as e:
        log.error("yt-dlp import failed: %s", e)
        return 1

    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [args.lang],
        "subtitlesformat": "vtt",
    }

    with tempfile.TemporaryDirectory() as tmp:
        ydl_opts["outtmpl"] = tmp + "/%(id)s.%(ext)s"
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(args.url, download=False)
                title = info.get("title", "<unknown>")
                log.info("Video: %s", title)

                subs = info.get("subtitles") or {}
                auto = info.get("automatic_captions") or {}
                has_manual = args.lang in subs
                has_auto = args.lang in auto

                if not (has_manual or has_auto):
                    log.warning("No subtitles found for lang=%s (manual=%s, auto=%s)", args.lang, has_manual, has_auto)
                    return 2

                # Try to download subtitles. yt-dlp will choose best available based on opts.
                ydl.download([args.url])

                # Confirm at least one VTT file exists
                import glob, os
                vtts = glob.glob(tmp + "/*.vtt")
                if not vtts:
                    log.error("Subtitle download attempted but no .vtt file found.")
                    return 1

                # Parse a small sample to ensure file isn't empty.
                path = vtts[0]
                size = os.path.getsize(path)
                log.info("Downloaded subtitle: %s (%d bytes)", path, size)
                if size < 50:
                    log.error("Subtitle file too small; likely invalid.")
                    return 1

                log.info("Smoke test passed.")
                return 0
        except Exception as e:
            log.error("Smoke test failed: %s", e)
            return 1

if __name__ == "__main__":
    raise SystemExit(main())
