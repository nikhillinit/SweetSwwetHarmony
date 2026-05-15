@echo off
cd /d "C:\dev\Harmonic"
set "JOB_POSTING_DOMAINS=betterhelp.com,calm.com,faire.com"
for /f %%I in ('powershell -NoProfile -Command "[DateTime]::UtcNow.ToString([string][char]111)"') do set "KEEPALIVE_RUN_START_UTC=%%I"
set "KEEPALIVE_ARTIFACT=C:\dev\Harmonic\artifacts\keepalive\%KEEPALIVE_RUN_START_UTC:~0,10%-HarmonicKeepAlive.json"
set "KEEPALIVE_WATCHDOG_ARTIFACT=C:\dev\Harmonic\artifacts\keepalive\%KEEPALIVE_RUN_START_UTC:~0,10%-HarmonicKeepAlive.watchdog.json"
call "python" run_pipeline.py collect --collectors job_postings
set "KEEPALIVE_COLLECT_EXIT=%ERRORLEVEL%"
call "python" scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 12 --operational greenhouse_jobs,ashby_jobs --min-created-at "%KEEPALIVE_RUN_START_UTC%" > "%KEEPALIVE_WATCHDOG_ARTIFACT%"
set "KEEPALIVE_WATCHDOG_EXIT=%ERRORLEVEL%"
call "python" scripts/red-team-hybrid/keepalive_verdict.py compose --mode daily_heartbeat --collector-exit "%KEEPALIVE_COLLECT_EXIT%" --watchdog-json "%KEEPALIVE_WATCHDOG_ARTIFACT%" --artifact "%KEEPALIVE_ARTIFACT%" --task-name "HarmonicKeepAlive"
set "KEEPALIVE_COMPOSE_EXIT=%ERRORLEVEL%"
set "KEEPALIVE_MONITOR_EXIT=0"
call "python" scripts/red-team-hybrid/keepalive_monitor_ping.py --artifact-json "%KEEPALIVE_ARTIFACT%" --task-name "HarmonicKeepAlive" --ping-url-env "HARMONIC_KEEPALIVE_PING_URL"
set "KEEPALIVE_MONITOR_EXIT=%ERRORLEVEL%"
call "python" scripts/red-team-hybrid/keepalive_verdict.py finalize --artifact "%KEEPALIVE_ARTIFACT%" --monitor-exit "%KEEPALIVE_MONITOR_EXIT%"
set "KEEPALIVE_FINAL_EXIT=%ERRORLEVEL%"
exit /b %KEEPALIVE_FINAL_EXIT%
