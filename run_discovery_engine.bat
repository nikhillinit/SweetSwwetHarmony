@echo off
cd /d C:\dev\Harmonic

REM Load environment variables from .env file
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    set "line=%%a"
    if not "!line:~0,1!"=="#" (
        set "%%a=%%b"
    )
)

REM Run the discovery engine
python -m discovery_engine.mcp_server
