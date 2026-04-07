# UserPromptSubmit hook — injects Harmonic session context before Claude sees the prompt.
# Stdout becomes part of Claude's context. Non-blocking (exit 0 always).
# Purpose: force branch -> canonical plan resolution at every turn, not just session start.
# See feedback_ownership_vs_laziness.md for why.

$ErrorActionPreference = 'Continue'
$null = [Console]::In.ReadToEnd()  # drain stdin; we don't parse it

$repo = $env:CLAUDE_PROJECT_DIR
if (-not $repo -or -not (Test-Path $repo)) { $repo = 'C:\dev\Harmonic' }
if (-not (Test-Path $repo)) { exit 0 }

Set-Location $repo

# --- Resolve branch ---
$branch = ''
try { $branch = (& git branch --show-current 2>$null).Trim() } catch { $branch = '' }
if (-not $branch) { exit 0 }

# --- Resolve canonical plan for prep/* or release/* branches ---
$planLine = ''
if ($branch -match '^(prep|release)/(.+?)(-prep)?$') {
    $topic = $matches[2]
    $plansDir = Join-Path $repo 'docs\plans'
    if (Test-Path $plansDir) {
        $candidates = @(Get-ChildItem -Path $plansDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match [regex]::Escape($topic) })
        if ($candidates.Count -eq 1) {
            $planLine = "Canonical plan: docs/plans/$($candidates[0].Name)/00-strategy.md (or README.md)"
        } elseif ($candidates.Count -gt 1) {
            $planLine = "Canonical plan: AMBIGUOUS ($($candidates.Count) candidates match '$topic'); check docs/plans/README.md for the flagged canonical"
        } else {
            $planLine = "Canonical plan: no docs/plans/*$topic* dir found"
        }
    }
}

# --- Protected paths reminder (only on prep branches during Move 0) ---
$protectedLine = ''
if ($branch -eq 'prep/red-team-hybrid-prep') {
    $protectedLine = "Protected paths (Move 0, ends 2026-04-19): collectors/ workflows/ governance/ monitoring/ connectors/ storage/migrations/"
}

# --- Emit context block ---
Write-Output ""
Write-Output "[harmonic session context]"
Write-Output "Branch: $branch"
if ($planLine)      { Write-Output $planLine }
if ($protectedLine) { Write-Output $protectedLine }
Write-Output "Ownership rules (feedback memory: feedback_ownership_vs_laziness.md):"
Write-Output "  1. Before proposing infrastructure: Glob/Read existing config first."
Write-Output "  2. Before claiming done: run the verification command + paste output."
Write-Output "  3. Before modifying a function: Read it in THIS conversation first."
Write-Output "[end context]"
Write-Output ""

exit 0
