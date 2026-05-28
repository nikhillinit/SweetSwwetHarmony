<#
.SYNOPSIS
    Installs the Harmonic Discovery Engine daily keep-alive scheduled task.

.DESCRIPTION
    Closes the R19 root cause by scheduling a daily local run of:
      1. python run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds
      2. python scripts/red-team-hybrid/freshness_watchdog.py --json
         (output appended to artifacts/keepalive/YYYY-MM-DD-<TaskName>.json)

    This script must be run ONCE per machine. It is idempotent -- re-running
    unregisters the previous task before re-registering, so no duplicates
    accumulate.

    Context: R19 (see docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md)
    was root-caused as "no scheduled collection". LIV-01 restarted the pipeline
    once; without a scheduler, R19 recurs within 36 hours. CONTEXT.md D-22
    mandates this local installer (not a GH Actions workflow) because the CI
    DB is a separate instance from the local production signals.db per
    .planning/phases/01-move-0-prep-liveness-prep/1-RESEARCH.md Finding 1.

    R19 is the ROOT cause closed by this script ("no scheduled collection"),
    not just the symptom (one-shot manual restart from LIV-01).

.PARAMETER TaskName
    Scheduled task name. Default: HarmonicKeepAlive.

.PARAMETER RunAt
    Time-of-day to run the task (24h HH:MM). Default: 08:00 (local).

.PARAMETER ProjectRoot
    Absolute path to the Harmonic repo root. Default: current directory.

.PARAMETER PythonExe
    Path to python executable. Default: python (resolved from PATH). If you
    use a venv, pass the venv's python.exe absolute path here so the
    scheduled task picks up your venv environment (and your .env).

.PARAMETER Collectors
    Comma-separated collector list for the run_pipeline.py collect step.
    Default is the operational daily keepalive set.

.PARAMETER WatchdogOperational
    Comma-separated source_api list passed to freshness_watchdog.py
    --operational. Default is the operational daily keepalive set.

.PARAMETER WatchdogThresholdHours
    Freshness threshold passed to freshness_watchdog.py --threshold-hours.
    Default preserves the original 36-hour watchdog behavior.

.PARAMETER VerdictMode
    Composite keepalive verdict mode. Defaults to daily_heartbeat for
    HarmonicKeepAlive and strict_write_proof for sibling drill tasks.
    daily_heartbeat treats duplicate-only no_post_run_rows DB proof as a
    non-fatal WARN_DUPLICATE_ONLY when collection and monitor delivery pass.
    strict_write_proof keeps no_post_run_rows as a hard failure.

.PARAMETER JobPostingDomains
    Optional comma-separated domain fixture list exported as JOB_POSTING_DOMAINS
    inside the generated runner. Use this for omission drills where
    job_postings is the positive DB-progress control.

.PARAMETER IgnoreWatchdogExitCode
    If specified, the generated cmd runner exits 0 after writing the watchdog
    JSON even when freshness_watchdog.py exits non-zero. Use this only for
    omission drills where the watchdog FAIL status is the expected evidence and
    Task Scheduler retries would muddy the observation window.

.PARAMETER MonitorPingUrlEnvVar
    Optional environment variable name containing a Healthchecks.io-compatible
    ping URL. When set, the generated runner posts the pre-monitor composite
    artifact through keepalive_monitor_ping.py after every run. The helper
    appends the composite pre-monitor exit status to the ping URL and includes
    runner plus DB freshness proof fields in the POST body. Use an environment
    variable rather than embedding the URL in the generated runner because ping
    URLs are secrets.

.PARAMETER MonitorAlertVerified
    Required for live HarmonicKeepAlive registration. Confirms that the ping
    URL configured in MonitorPingUrlEnvVar alerts a real human on missed or
    failed runs. This is an operator assertion; GenerateOnly previews do not
    require it.

.PARAMETER HostMode
    Required for live HarmonicKeepAlive registration. Use LocalHost for a
    provisional local-machine trial or DedicatedHost for an always-on host
    trial. This scopes the claim made after the run; it does not close Phase
    5.2 durability.

.PARAMETER GenerateOnly
    Writes the inner runner cmd file, prints its path, and exits before any
    ScheduledTasks cmdlet is called. Use this for tests and preview generation.

.PARAMETER TestRun
    If specified, triggers the task immediately after registering, so the
    first artifacts/keepalive/YYYY-MM-DD-<TaskName>.json is produced without waiting
    until 08:00 tomorrow.

.EXAMPLE
    .\install_keepalive_task.ps1
    .\install_keepalive_task.ps1 -TestRun
    .\install_keepalive_task.ps1 -RunAt "07:30" -TestRun
    .\install_keepalive_task.ps1 -PythonExe "C:\dev\Harmonic\.venv\Scripts\python.exe" -TestRun
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install_keepalive_task.ps1 -TaskName "HarmonicFreezeDrillPreview" -Collectors "job_postings,github" -WatchdogOperational "rss_feeds,greenhouse_jobs,ashby_jobs" -WatchdogThresholdHours 12 -JobPostingDomains "10beauty.com,cofertility.com,openai.com" -IgnoreWatchdogExitCode -GenerateOnly

.NOTES
    REQ: Phase 1 keep-alive (CONTEXT.md D-22, R19 root cause fix)
    Plan: .planning/phases/01-move-0-prep-liveness-prep/01-05-PLAN.md
    Evidence: artifacts/keepalive/YYYY-MM-DD-<TaskName>.json per run
    Hard rubric gate 5 requires at least 2 successful runs captured before
    2026-04-18 (Step 4B regret check).

    Date format assumption: the inner cmd.exe wrapper uses
    %DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2% which extracts YYYY-MM-DD assuming
    the machine's locale renders DATE as "Day MM/DD/YYYY". On non-en-US
    locales the slice positions may shift; the task still runs but the
    filename layout drifts. Document and adjust per machine.

    Active drill safety: before the Monday, 2026-05-11 readout, do not invoke
    this installer against the live repo/task with -TaskName HarmonicFreezeDrill.
    Doing so can rewrite the live wrapper and/or re-register the active task.

    news_api is optional enrichment, not a default freshness dependency. Its
    intended gap is mainstream press, funding announcements, and PR activity,
    but the current GNews-backed collector is quota-constrained. Revisit with
    a provider swap or a manual weekly run before promoting it back into the
    watchdog gate.
#>

[CmdletBinding()]
param(
    [string]$TaskName = "HarmonicKeepAlive",
    [string]$RunAt = "08:00",
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$PythonExe = "python",
    [string]$Collectors = "hacker_news,arxiv,rss_feeds",
    [string]$WatchdogOperational = "hacker_news,arxiv,rss_feeds",
    [double]$WatchdogThresholdHours = 36,
    [ValidateSet("", "daily_heartbeat", "strict_write_proof")]
    [string]$VerdictMode = "",
    [string]$JobPostingDomains = "",
    [switch]$IgnoreWatchdogExitCode,
    [string]$MonitorPingUrlEnvVar = "",
    [switch]$MonitorAlertVerified,
    [ValidateSet("", "LocalHost", "DedicatedHost")]
    [string]$HostMode = "",
    [switch]$GenerateOnly,
    [switch]$TestRun
)

$ErrorActionPreference = "Stop"

if ($PythonExe.Contains('"')) {
    throw "PythonExe must not contain double quotes."
}
$PythonCmd = "call ""$PythonExe"""
if (-not $VerdictMode.Trim()) {
    $VerdictMode = if ($TaskName -eq "HarmonicKeepAlive") { "daily_heartbeat" } else { "strict_write_proof" }
}

$LiveKeepAliveMutation = $TaskName -eq "HarmonicKeepAlive" -and -not $GenerateOnly
if ($LiveKeepAliveMutation) {
    if (-not $HostMode.Trim()) {
        throw "HostMode is required for live HarmonicKeepAlive registration. Use -HostMode LocalHost or -HostMode DedicatedHost."
    }
    if (-not $MonitorPingUrlEnvVar.Trim()) {
        throw "MonitorPingUrlEnvVar is required for live HarmonicKeepAlive registration."
    }
    if (-not $MonitorAlertVerified) {
        throw "MonitorAlertVerified is required for live HarmonicKeepAlive registration after human alert delivery is verified."
    }
    if (-not [Environment]::GetEnvironmentVariable($MonitorPingUrlEnvVar)) {
        throw "Environment variable $MonitorPingUrlEnvVar is not set; live HarmonicKeepAlive registration requires a monitor ping URL."
    }
}

# Resolve absolute paths for everything so the scheduled task is location-stable
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$ArtifactsDir = Join-Path $ProjectRoot "artifacts\keepalive"
if (-not (Test-Path $ArtifactsDir)) {
    New-Item -ItemType Directory -Path $ArtifactsDir -Force | Out-Null
    Write-Host "Created artifacts directory: $ArtifactsDir"
}

# The inner command is a small cmd.exe batch script that runs both commands
# sequentially. Collection runs first; freshness watchdog runs regardless
# (even if collect errors) so we always capture a freshness snapshot.
#
# Why cmd.exe instead of pure PowerShell: scheduled-task output redirection
# to a date-stamped file is more reliable through cmd than through
# powershell piped-redirection, especially across user-context and
# system-context execution boundaries.
$PipelineCmd = "$PythonCmd run_pipeline.py collect --collectors $Collectors"
$WatchdogCmd = "$PythonCmd scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours $WatchdogThresholdHours"
if ($WatchdogOperational.Trim()) {
    $WatchdogCmd = "$WatchdogCmd --operational $WatchdogOperational"
}
$WatchdogCmd = "$WatchdogCmd --min-created-at ""%KEEPALIVE_RUN_START_UTC%"""
$VerdictComposeCmd = "$PythonCmd scripts/red-team-hybrid/keepalive_verdict.py compose --mode $VerdictMode --collector-exit ""%KEEPALIVE_COLLECT_EXIT%"" --watchdog-json ""%KEEPALIVE_WATCHDOG_ARTIFACT%"" --artifact ""%KEEPALIVE_ARTIFACT%"" --task-name ""$TaskName"""
$VerdictFinalizeCmd = "$PythonCmd scripts/red-team-hybrid/keepalive_verdict.py finalize --artifact ""%KEEPALIVE_ARTIFACT%"" --monitor-exit ""%KEEPALIVE_MONITOR_EXIT%"""

$EnvLines = @()
if ($JobPostingDomains.Trim()) {
    $EnvLines += "set ""JOB_POSTING_DOMAINS=$JobPostingDomains"""
}

$MonitorLines = @()
$MonitorLines += 'set "KEEPALIVE_MONITOR_EXIT=0"'
if ($MonitorPingUrlEnvVar.Trim()) {
    $MonitorLines += "$PythonCmd scripts/red-team-hybrid/keepalive_monitor_ping.py --artifact-json ""%KEEPALIVE_ARTIFACT%"" --task-name ""$TaskName"" --ping-url-env ""$MonitorPingUrlEnvVar"""
    $MonitorLines += "set ""KEEPALIVE_MONITOR_EXIT=%ERRORLEVEL%"""
}

$ExitLines = @()
if ($IgnoreWatchdogExitCode) {
    if ($MonitorPingUrlEnvVar.Trim()) {
        $ExitLines += 'if not "%KEEPALIVE_MONITOR_EXIT%"=="0" exit /b %KEEPALIVE_MONITOR_EXIT%'
    }
    $ExitLines += 'exit /b 0'
} else {
    $ExitLines += 'exit /b %KEEPALIVE_FINAL_EXIT%'
}

$SafeTaskName = ($TaskName -replace '[^A-Za-z0-9_-]', '_')

$DailyScript = @"
@echo off
cd /d "$ProjectRoot"
$($EnvLines -join "`r`n")
for /f %%I in ('powershell -NoProfile -Command "[DateTime]::UtcNow.ToString([string][char]111)"') do set "KEEPALIVE_RUN_START_UTC=%%I"
set "KEEPALIVE_ARTIFACT=$ArtifactsDir\%KEEPALIVE_RUN_START_UTC:~0,10%-$SafeTaskName.json"
set "KEEPALIVE_WATCHDOG_ARTIFACT=$ArtifactsDir\%KEEPALIVE_RUN_START_UTC:~0,10%-$SafeTaskName.watchdog.json"
$PipelineCmd
set "KEEPALIVE_COLLECT_EXIT=%ERRORLEVEL%"
$WatchdogCmd > "%KEEPALIVE_WATCHDOG_ARTIFACT%"
set "KEEPALIVE_WATCHDOG_EXIT=%ERRORLEVEL%"
$VerdictComposeCmd
set "KEEPALIVE_COMPOSE_EXIT=%ERRORLEVEL%"
$($MonitorLines -join "`r`n")
$VerdictFinalizeCmd
set "KEEPALIVE_FINAL_EXIT=%ERRORLEVEL%"
$($ExitLines -join "`r`n")
"@

$ScriptName = if ($TaskName -eq "HarmonicKeepAlive") { "_keepalive_daily.cmd" } else { "_keepalive_$SafeTaskName.cmd" }
$ScriptPath = Join-Path $ProjectRoot "scripts\red-team-hybrid\$ScriptName"
Set-Content -Path $ScriptPath -Value $DailyScript -Encoding ASCII
Write-Host "Wrote inner runner: $ScriptPath"
if ($GenerateOnly) {
    Write-Host "GenerateOnly specified; skipping all ScheduledTasks cmdlets."
    Write-Host "Task name preview: $TaskName"
    Write-Host "Evidence directory: $ArtifactsDir"
    Write-Host "Inner script:       $ScriptPath"
    return
}

# Build the scheduled task primitives. All stdlib ScheduledTasks cmdlets;
# no Install-Module, no third-party dependencies, no admin elevation.
$Action = New-ScheduledTaskAction -Execute "cmd.exe" `
                                  -Argument "/c `"$ScriptPath`"" `
                                  -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $RunAt

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" `
                                        -LogonType Interactive `
                                        -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
                -StartWhenAvailable `
                -RestartCount 2 `
                -RestartInterval (New-TimeSpan -Minutes 15) `
                -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries

# Idempotent: remove existing task if present, then re-register. This makes
# the installer safe to re-run during Phase 1 debugging without the
# scheduler accumulating duplicate tasks.
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Removing existing scheduled task: $TaskName"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Write-Host "Registering scheduled task: $TaskName"
Register-ScheduledTask -TaskName $TaskName `
                       -Action $Action `
                       -Trigger $Trigger `
                       -Principal $Principal `
                       -Settings $Settings `
                       -Description "Harmonic Discovery Engine daily keep-alive (R19 root cause fix per CONTEXT.md D-22)" `
                       -Force | Out-Null

Write-Host "Task registered. Scheduled to run daily at $RunAt local time."
if ($LiveKeepAliveMutation) {
    Write-Host "Live re-enable gate satisfied. HostMode: $HostMode. Monitor env var: $MonitorPingUrlEnvVar."
}
Write-Host "Evidence directory: $ArtifactsDir"
Write-Host "Inner script:       $ScriptPath"

if ($TestRun) {
    Write-Host ""
    Write-Host "Triggering immediate test run..."
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Test run triggered. Check $ArtifactsDir in ~60 seconds for the JSON output."
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Verify task exists:           Get-ScheduledTask -TaskName $TaskName"
Write-Host "  2. Verify first run produced:    Get-ChildItem $ArtifactsDir"
Write-Host "  3. Check exit status of last:    (Get-ScheduledTaskInfo -TaskName $TaskName).LastTaskResult"
Write-Host "  4. Hard rubric gate 5 requires >= 2 successful runs captured before 2026-04-18."
