<#
.SYNOPSIS
    Installs a one-shot verifier for the next HarmonicKeepAlive composite artifact.

.DESCRIPTION
    Registers a one-time scheduled task that runs after the next
    HarmonicKeepAlive cycle and verifies that the expected artifact is a
    finalized composite verdict. The verifier fails closed on raw watchdog
    artifacts, unfinalized composites, monitor delivery failure, hard DB proof
    failure, or non-zero final exit status.

.PARAMETER TaskName
    Scheduled task name for the verifier. Default:
    HarmonicKeepAliveCompositeVerify.

.PARAMETER KeepAliveTaskName
    Keepalive task whose next run should be verified. Default:
    HarmonicKeepAlive.

.PARAMETER VerifyAt
    Optional one-shot verifier time. If omitted, the installer uses
    KeepAliveTaskName's NextRunTime plus DelayMinutes.

.PARAMETER KeepAliveNextRun
    Optional keepalive run time used to derive the expected UTC artifact date.
    This is mainly for GenerateOnly previews and tests. If omitted, the
    scheduled task's NextRunTime is used.

.PARAMETER DelayMinutes
    Minutes after the keepalive run to run verification. Default: 30.

.PARAMETER GenerateOnly
    Writes the verifier cmd wrapper and exits before ScheduledTasks mutation.
#>

[CmdletBinding()]
param(
    [string]$TaskName = "HarmonicKeepAliveCompositeVerify",
    [string]$KeepAliveTaskName = "HarmonicKeepAlive",
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$PythonExe = "python",
    [string]$VerifyAt = "",
    [string]$KeepAliveNextRun = "",
    [int]$DelayMinutes = 30,
    [switch]$GenerateOnly
)

$ErrorActionPreference = "Stop"

if ($PythonExe.Contains('"')) {
    throw "PythonExe must not contain double quotes."
}

function Parse-LocalDateTime {
    param(
        [string]$Value,
        [string]$Label
    )

    try {
        return [DateTime]::Parse(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeLocal
        )
    } catch {
        throw "$Label must be a parseable date/time. Received: $Value"
    }
}

$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$ArtifactsDir = Join-Path $ProjectRoot "artifacts\keepalive"
if (-not (Test-Path $ArtifactsDir)) {
    New-Item -ItemType Directory -Path $ArtifactsDir -Force | Out-Null
    Write-Host "Created artifacts directory: $ArtifactsDir"
}

$TaskInfo = $null
if (-not $KeepAliveNextRun.Trim() -or -not $VerifyAt.Trim()) {
    $TaskInfo = Get-ScheduledTaskInfo -TaskName $KeepAliveTaskName -ErrorAction SilentlyContinue
}

if ($KeepAliveNextRun.Trim()) {
    $KeepAliveRunAt = Parse-LocalDateTime -Value $KeepAliveNextRun -Label "KeepAliveNextRun"
} elseif ($TaskInfo -and $TaskInfo.NextRunTime -gt [DateTime]::MinValue) {
    $KeepAliveRunAt = $TaskInfo.NextRunTime
} else {
    throw "Could not resolve next run for $KeepAliveTaskName. Pass -KeepAliveNextRun for previews or re-register the keepalive task first."
}

if ($VerifyAt.Trim()) {
    $VerifyAtDate = Parse-LocalDateTime -Value $VerifyAt -Label "VerifyAt"
} else {
    $VerifyAtDate = $KeepAliveRunAt.AddMinutes($DelayMinutes)
}

$ArtifactDate = $KeepAliveRunAt.ToUniversalTime().ToString(
    "yyyy-MM-dd",
    [System.Globalization.CultureInfo]::InvariantCulture
)
$ReportPath = Join-Path $ArtifactsDir "$ArtifactDate-$KeepAliveTaskName-composite-verification.json"
$OkPath = Join-Path $ArtifactsDir "$ArtifactDate-$KeepAliveTaskName-composite-verification.ok.txt"
$ActionPath = Join-Path $ArtifactsDir "$ArtifactDate-$KeepAliveTaskName-composite-verification.action-required.txt"
$PythonCmd = "call ""$PythonExe"""
$VerifyCmd = "$PythonCmd scripts/red-team-hybrid/verify_keepalive_composite_artifact.py --artifact-dir ""$ArtifactsDir"" --task-name ""$KeepAliveTaskName"" --date ""$ArtifactDate"" --mode daily_heartbeat --report ""$ReportPath"""

$SafeTaskName = ($TaskName -replace '[^A-Za-z0-9_-]', '_')
$ScriptPath = Join-Path $ProjectRoot "scripts\red-team-hybrid\_$SafeTaskName.cmd"

$VerifierScript = @"
@echo off
cd /d "$ProjectRoot"
$VerifyCmd
set "VERIFY_EXIT=%ERRORLEVEL%"
if "%VERIFY_EXIT%"=="0" (
  echo Composite keepalive artifact verified for $KeepAliveTaskName on $ArtifactDate. > "$OkPath"
) else (
  echo Composite keepalive verification requires operator review for $KeepAliveTaskName on $ArtifactDate. > "$ActionPath"
)
exit /b %VERIFY_EXIT%
"@

Set-Content -Path $ScriptPath -Value $VerifierScript -Encoding ASCII
Write-Host "Wrote verifier runner: $ScriptPath"
Write-Host "Expected artifact date: $ArtifactDate"
Write-Host "Verification report:   $ReportPath"

if ($GenerateOnly) {
    Write-Host "GenerateOnly specified; skipping all ScheduledTasks cmdlets."
    Write-Host "Verifier task preview: $TaskName"
    Write-Host "Verifier time:         $VerifyAtDate"
    return
}

$Action = New-ScheduledTaskAction -Execute "cmd.exe" `
                                  -Argument "/c `"$ScriptPath`"" `
                                  -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -Once -At $VerifyAtDate

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" `
                                        -LogonType Interactive `
                                        -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
                -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Removing existing scheduled task: $TaskName"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Write-Host "Registering one-shot verification task: $TaskName"
Register-ScheduledTask -TaskName $TaskName `
                       -Action $Action `
                       -Trigger $Trigger `
                       -Principal $Principal `
                       -Settings $Settings `
                       -Description "One-shot verification that HarmonicKeepAlive produced a finalized composite keepalive artifact." `
                       -Force | Out-Null

Write-Host "Verifier registered. Scheduled for $VerifyAtDate local time."
Write-Host "Check result: Get-ScheduledTaskInfo -TaskName $TaskName"
