# yt-dlp maintenance policy (practical + reproducible)

`yt-dlp` is a “volatile dependency”: upstream websites change, and older builds can break unexpectedly. On Windows, you want a policy that is:

- predictable (reproducible installs)
- low-effort (doesn’t require constant babysitting)
- safe (doesn’t block your core ops path)

## Recommended strategy

### 1) Pin `yt-dlp` to a known-good version
Instead of `yt-dlp>=...`, pin to an exact version you’ve smoke tested:

```text
yt-dlp==<known_good_version>
```

This prevents “it worked yesterday” surprises.

### 2) Keep YouTube optional
Treat YouTube subtitle collection as best-effort. If it breaks, your ops layer should still run.

### 3) Upgrade only when needed (or on a cadence)
Two reasonable triggers:

- **Reactive**: the smoke test fails → upgrade
- **Cadence**: upgrade monthly/quarterly, even if it’s working

## Upgrade procedure (Windows)

1. Activate your venv
2. Run the smoke test:

```powershell
python scripts\ytdlp_smoke_test.py --url "https://www.youtube.com/watch?v=<VIDEO_ID>"
```

3. Upgrade yt-dlp:

```powershell
pip install --upgrade yt-dlp
pip show yt-dlp
```

4. Re-run the smoke test. If it passes, capture the version and update your pinned dependency.

## What the smoke test should validate

- `yt-dlp` can extract metadata for a typical public video
- you can retrieve an English subtitle track (manual or auto) when available
- subtitle download + parse succeeds
- failure mode prints a helpful message and returns non-zero

This doc set includes a basic smoke test script to start from.
