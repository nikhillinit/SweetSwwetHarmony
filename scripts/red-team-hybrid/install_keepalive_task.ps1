<#
.SYNOPSIS
    Installs the Harmonic Discovery Engine daily keep-alive scheduled task.

.DESCRIPTION
    Closes the R19 root cause by scheduling a daily local run of:
      1. python run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api
      2. python scripts/red-team-hybrid/freshness_watchdog.py --json
         (output appended to artifacts/keepalive/YYYY-MM-DD.json)

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
    Default preserves the original daily keepalive set.

.PARAMETER WatchdogOperational
    Comma-separated source_api list passed to freshness_watchdog.py
    --operational. Default preserves the original watchdog operational set.

.PARAMETER WatchdogThresholdHours
    Freshness threshold passed to freshness_watchdog.py --threshold-hours.
    Default preserves the original 36-hour watchdog behavior.

.PARAMETER JobPostingDomains
    Optional comma-separated domain fixture list exported as JOB_POSTING_DOMAINS
    inside the generated runner. Use this for omission drills where
    job_postings is the positive DB-progress control.

.PARAMETER IgnoreWatchdogExitCode
    If specified, the generated cmd runner exits 0 after writing the watchdog
    JSON even when freshness_watchdog.py exits non-zero. Use this only for
    omission drills where the watchdog FAIL status is the expected evidence and
    Task Scheduler retries would muddy the observation window.

.PARAMETER GenerateOnly
    Writes the inner runner cmd file, prints its path, and exits before any
    ScheduledTasks cmdlet is called. Use this for tests and preview generation.

.PARAMETER TestRun
    If specified, triggers the task immediately after registering, so the
    first artifacts/keepalive/YYYY-MM-DD.json is produced without waiting
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
    Evidence: artifacts/keepalive/YYYY-MM-DD.json per run
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
#>

[CmdletBinding()]
param(
    [string]$TaskName = "HarmonicKeepAlive",
    [string]$RunAt = "08:00",
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$PythonExe = "python",
    [string]$Collectors = "hacker_news,arxiv,rss_feeds,news_api",
    [string]$WatchdogOperational = "hacker_news,arxiv,rss_feeds,news_api",
    [double]$WatchdogThresholdHours = 36,
    [string]$JobPostingDomains = "",
    [switch]$IgnoreWatchdogExitCode,
    [switch]$GenerateOnly,
    [switch]$TestRun
)

$ErrorActionPreference = "Stop"

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
# to a date-stamped file is far more reliable through cmd than through
# powershell piped-redirection, especially across user-context and
# system-context execution boundaries.
$PipelineCmd = "$PythonExe run_pipeline.py collect --collectors $Collectors"
$WatchdogCmd = "$PythonExe scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours $WatchdogThresholdHours"
if ($WatchdogOperational.Trim()) {
    $WatchdogCmd = "$WatchdogCmd --operational $WatchdogOperational"
}

$EnvLines = @()
if ($JobPostingDomains.Trim()) {
    $EnvLines += "set ""JOB_POSTING_DOMAINS=$JobPostingDomains"""
}

$ExitLine = if ($IgnoreWatchdogExitCode) { "exit /b 0" } else { "" }

$DailyScript = @"
@echo off
cd /d "$ProjectRoot"
$($EnvLines -join "`r`n")
$PipelineCmd
$WatchdogCmd > "$ArtifactsDir\%DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%.json"
$ExitLine
"@

$SafeTaskName = ($TaskName -replace '[^A-Za-z0-9_-]', '_')
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
