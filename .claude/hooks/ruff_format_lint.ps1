# PostToolUse hook -- ruff format/lint for edited Python files.
#
# Fires after Edit/Write. Acts ONLY on *.py files inside the repo. Designed to add
# linter visibility to a codebase that has no formatter/linter wired, WITHOUT forcing
# reformat churn on legacy files the first time they're touched.
#
# Modes (env HARMONIC_RUFF_MODE):
#   fix (default)      -- apply `ruff format` + `ruff check --fix` to the edited file.
#   advisory           -- report what ruff WOULD change/flag; never edits, never blocks.
#
# Escalation (env HARMONIC_RUFF_BLOCK=1):
#   In advisory mode, surface findings as a blocking PostToolUse error (exit 2) that
#   Claude must address, instead of a non-blocking breadcrumb.
#
# Skips silently if ruff is not on PATH (so it's a no-op in environments without ruff).

$ErrorActionPreference = 'Continue'

$raw = [Console]::In.ReadToEnd()
if (-not $raw) { exit 0 }
try { $payload = $raw | ConvertFrom-Json } catch { exit 0 }

$toolName = [string]$payload.tool_name
if ($toolName -ne 'Edit' -and $toolName -ne 'Write') { exit 0 }

$file = [string]$payload.tool_input.file_path
if (-not $file) { exit 0 }
if ($file -notmatch '\.py$') { exit 0 }
if (-not (Test-Path $file)) { exit 0 }

if (-not (Get-Command ruff -ErrorAction SilentlyContinue)) { exit 0 }  # ruff absent -> no-op

$mode  = if ($env:HARMONIC_RUFF_MODE) { $env:HARMONIC_RUFF_MODE.ToLowerInvariant() } else { 'fix' }
$block = ($env:HARMONIC_RUFF_BLOCK -eq '1')

if ($mode -eq 'fix') {
    $fmtOut = (& ruff format "$file" 2>&1) -join ' '
    $chkOut = (& ruff check --fix "$file" 2>&1) -join ' '
    $parts = @("[ruff] $file")
    if ($fmtOut -match 'reformatted') { $parts += 'reformatted' } else { $parts += 'format-clean' }
    if ($chkOut -match '(\d+)\s+fixed') { $parts += ("fixed " + $matches[1] + " lint issue(s)") }
    [Console]::Error.WriteLine(($parts -join ' | '))
    exit 0
}

# --- advisory mode: report only, no edits ---
$fmt = & ruff format --check "$file" 2>&1
$fmtExit = $LASTEXITCODE
$lint = & ruff check "$file" 2>&1
$lintExit = $LASTEXITCODE

if ($fmtExit -eq 0 -and $lintExit -eq 0) { exit 0 }  # clean

$lines = @("[ruff advisory] $file  (set HARMONIC_RUFF_MODE=fix to auto-apply)")
if ($fmtExit -ne 0) {
    $lines += "  - formatting: 'ruff format' would reformat this file"
}
if ($lintExit -ne 0) {
    $lines += "  - lint:"
    $count = 0
    foreach ($l in @($lint)) {
        if (-not $l) { continue }
        if ($count -ge 20) { $lines += "      ... (truncated; run: ruff check `"$file`")"; break }
        $lines += "      $l"
        $count++
    }
}
$msg = $lines -join "`n"

[Console]::Error.WriteLine($msg)
if ($block) { exit 2 }  # blocking escalation
exit 0                  # advisory: visible, non-blocking
