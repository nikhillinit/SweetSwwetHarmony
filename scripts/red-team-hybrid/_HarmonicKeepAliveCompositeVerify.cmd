@echo off
cd /d "C:\dev\Harmonic"
call "python" scripts/red-team-hybrid/verify_keepalive_composite_artifact.py --artifact-dir "C:\dev\Harmonic\artifacts\keepalive" --task-name "HarmonicKeepAlive" --date "2026-05-15" --mode daily_heartbeat --report "C:\dev\Harmonic\artifacts\keepalive\2026-05-15-HarmonicKeepAlive-composite-verification.json"
set "VERIFY_EXIT=%ERRORLEVEL%"
if "%VERIFY_EXIT%"=="0" (
  echo Composite keepalive artifact verified for HarmonicKeepAlive on 2026-05-15. > "C:\dev\Harmonic\artifacts\keepalive\2026-05-15-HarmonicKeepAlive-composite-verification.ok.txt"
) else (
  echo Composite keepalive verification requires operator review for HarmonicKeepAlive on 2026-05-15. > "C:\dev\Harmonic\artifacts\keepalive\2026-05-15-HarmonicKeepAlive-composite-verification.action-required.txt"
)
exit /b %VERIFY_EXIT%
