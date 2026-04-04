@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d C:\dev\Harmonic

REM Load environment variables from .env file
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "line=%%a"
        if defined line if not "!line:~0,1!"=="#" (
            set "%%a=%%b"
        )
    )
)

REM Run the discovery engine
python -m discovery_engine.mcp_server
endlocal
