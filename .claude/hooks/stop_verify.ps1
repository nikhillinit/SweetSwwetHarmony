# Stop hook — final verification gate before Claude ends the session.
# On the prep branch during Move 0, blocks Stop if check_protected_paths.sh reports
# any protected-path violations. Non-bypassable because no subsequent tool runs.
#
# Stop hooks return JSON on stdout with {"decision": "block", "reason": "..."} to block.
# Exit 0 with empty stdout = allow normal stop.

$ErrorActionPreference = 'Continue'
$null = [Console]::In.ReadToEnd()  # drain stdin

$repo = $env:CLAUDE_PROJECT_DIR
if (-not $repo -or -not (Test-Path $repo)) { $repo = 'C:\dev\Harmonic' }
if (-not (Test-Path $repo)) { exit 0 }

$guardScript = Join-Path $repo 'scripts\red-team-hybrid\check_protected_paths.sh'
if (-not (Test-Path $guardScript)) { exit 0 }

Set-Location $repo

# --- Only fire on the prep branch ---
$branch = ''
try { $branch = (& git branch --show-current 2>$null).Trim() } catch { $branch = '' }
if ($branch -ne 'prep/red-team-hybrid-prep') { exit 0 }

# --- Locate git bash ---
$bashPath = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
if (-not (Test-Path $bashPath)) { exit 0 }  # no bash, no guard, allow stop

# --- Run the guard ---
$guardOutput = & $bashPath -c "bash scripts/red-team-hybrid/check_protected_paths.sh 2>&1"
$guardExit = $LASTEXITCODE

if ($guardExit -ne 0) {
    $reasonLines = @(
        "Session attempting to stop with protected-path violations during Move 0 regret window.",
        "Window: 2026-04-06 -> 2026-04-19. Branch: prep/red-team-hybrid-prep.",
        "",
        "Guard output:",
        ""
    )
    $reasonLines += $guardOutput
    $reasonLines += @(
        "",
        "Revert the protected-path changes before stopping. See",
        "docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md for the allowed-paths list."
    )
    $reason = $reasonLines -join "`n"

    $payload = [ordered]@{
        decision = "block"
        reason   = $reason
    }
    $json = $payload | ConvertTo-Json -Compress -Depth 3
    Write-Output $json
    exit 0  # Stop-hook block is signaled via JSON decision, not exit code
}

exit 0
