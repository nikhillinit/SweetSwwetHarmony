# PostToolUse hook — runs after Edit/Write/Bash to catch protected-path violations.
# Only fires on the prep/red-team-hybrid-prep branch (Move 0 regret window).
# Wraps the existing scripts/red-team-hybrid/check_protected_paths.sh.
# Exit 2 + stderr signals a block/feedback to Claude Code.

$ErrorActionPreference = 'Continue'
$null = [Console]::In.ReadToEnd()  # drain stdin

$repo = $env:CLAUDE_PROJECT_DIR
if (-not $repo -or -not (Test-Path $repo)) { $repo = 'C:\dev\Harmonic' }
if (-not (Test-Path $repo)) { exit 0 }

$guardScript = Join-Path $repo 'scripts\red-team-hybrid\check_protected_paths.sh'
if (-not (Test-Path $guardScript)) { exit 0 }  # script not present — no-op

Set-Location $repo

# --- Only fire on the prep branch ---
$branch = ''
try { $branch = (& git branch --show-current 2>$null).Trim() } catch { $branch = '' }
if ($branch -ne 'prep/red-team-hybrid-prep') { exit 0 }

# --- Locate git bash ---
$bashPath = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
if (-not (Test-Path $bashPath)) {
    # No bash available — warn but don't block (false negatives are acceptable; false blocks aren't)
    [Console]::Error.WriteLine("[protected-paths-guard] WARN: git bash not found at $bashPath; skipping guard")
    exit 0
}

# --- Run the guard ---
$guardOutput = & $bashPath -c "bash scripts/red-team-hybrid/check_protected_paths.sh 2>&1"
$guardExit = $LASTEXITCODE

if ($guardExit -ne 0) {
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine("========================================================")
    [Console]::Error.WriteLine("PROTECTED PATH VIOLATION (Move 0 regret window)")
    [Console]::Error.WriteLine("========================================================")
    foreach ($line in $guardOutput) { [Console]::Error.WriteLine($line) }
    [Console]::Error.WriteLine("")
    [Console]::Error.WriteLine("ACTION REQUIRED:")
    [Console]::Error.WriteLine("  1. Revert the change to the protected path.")
    [Console]::Error.WriteLine("  2. If the change is intentional and pre-approved, consult")
    [Console]::Error.WriteLine("     docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md")
    [Console]::Error.WriteLine("     for the allowed-paths list and escalation procedure.")
    [Console]::Error.WriteLine("  3. Protected paths: collectors/ workflows/ governance/")
    [Console]::Error.WriteLine("     monitoring/ connectors/ storage/migrations/")
    [Console]::Error.WriteLine("========================================================")
    exit 2  # PostToolUse exit-2 feeds stderr back to Claude as a blocking error
}

exit 0
