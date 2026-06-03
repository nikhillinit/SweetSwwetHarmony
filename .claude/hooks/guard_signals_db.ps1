# PreToolUse hook -- signals.db write-guard.
#
# Fires on ALL branches (unlike the Move-0 protected-path hooks, which gate on
# prep/red-team-hybrid-prep inside their bodies). Purpose: stop Claude's own tool
# calls from overwriting or deleting the live signals.db or its recovery backups --
# the failure mode behind the 2026-05-05 909->4-row reversion (Issue #149 /
# MEMORY Known Issue #10), where a truncated backup was copied onto the live DB.
#
# Scope & limits (deliberately honest):
#   * Guards Claude's Bash / PowerShell / Write / filesystem-MCP tool calls only.
#   * Does NOT guard commands the operator types directly in their own terminal
#     (that was the actual incident vector). It narrows CLAUDE's blast radius.
#   * Decision is "ask" (user-overridable), never a hard deny -- a false positive
#     costs one keypress; a false negative could cost the corpus.
#
# Protocol: emit PreToolUse JSON on stdout with hookSpecificOutput.permissionDecision.
#   "ask"  -> user is prompted to confirm or cancel the tool call
#   exit 0 with no stdout -> allow (default)

$ErrorActionPreference = 'Continue'

# --- Read the tool-call payload from stdin ---
$raw = [Console]::In.ReadToEnd()
if (-not $raw) { exit 0 }

try {
    $payload = $raw | ConvertFrom-Json
} catch {
    exit 0  # unparseable -> never interfere
}

$toolName  = [string]$payload.tool_name
$toolInput = $payload.tool_input
if (-not $toolName) { exit 0 }

# --- Protected basenames: the live DB + its sidecars, plus any recovery backup ---
$liveDb = @('signals.db', 'signals.db-wal', 'signals.db-shm')

function Test-IsProtectedBasename {
    param([string]$Name)
    if (-not $Name) { return $false }
    $b = (Split-Path $Name -Leaf).ToLowerInvariant()
    if ($liveDb -contains $b) { return $true }
    if ($b -like 'signals.db.*') { return $true }   # signals.db.pre-*, .checkpoint-*, etc.
    return $false
}

# Last path-like operand of a copy/move command = its destination.
function Get-LastOperand {
    param([string]$Cmd)
    $tokens = [regex]::Matches($Cmd, '("[^"]*"|''[^'']*''|\S+)') | ForEach-Object { $_.Value.Trim('"', "'") }
    $cand = $null
    foreach ($t in $tokens) {
        if ($t -match '^-') { continue }   # flag like -Destination, --force
        if ($t -match '^(cp|copy|copy-item|mv|move|move-item|rm|del|erase|remove-item)$') { continue }
        $cand = $t
    }
    return $cand
}

$flag   = $false
$reason = ''

if ($toolName -eq 'Bash' -or $toolName -eq 'PowerShell') {
    $cmd = [string]$toolInput.command
    if ($cmd) {
        $low = $cmd.ToLowerInvariant()

        if ($low -match 'git\s+reset\s+--hard') {
            $flag = $true
            $reason = 'git reset --hard desyncs the working tree -- the 2026-05-05 incident co-occurred with a hard reset in the same shell session.'
        }
        elseif ($low -match 'git\s+clean\b[^|;&]*\s-[a-z]*x') {
            $flag = $true
            $reason = 'git clean -x deletes gitignored files, including the live signals.db.'
        }
        elseif ($low -match 'git\s+(checkout|restore)\b[^|;&]*signals\.db') {
            $flag = $true
            $reason = 'git checkout/restore targeting signals.db.'
        }
        elseif ($low -match '>>?\s*[^|;&]*signals\.db') {
            $flag = $true
            $reason = 'Shell redirection writing onto a signals.db path.'
        }
        elseif (($low -match '\b(rm|del|erase)\b') -or ($low -match 'remove-item')) {
            if ($low -match 'signals\.db') {
                $flag = $true
                $reason = 'Deletion command references a protected signals.db file (live DB or recovery backup).'
            }
        }
        elseif (($low -match '\b(cp|copy|mv|move)\b') -or ($low -match 'copy-item') -or ($low -match 'move-item')) {
            if ($low -match 'signals\.db') {
                $dest = Get-LastOperand $cmd
                if (Test-IsProtectedBasename $dest) {
                    $flag = $true
                    $reason = "Copy/move with a protected signals.db file as DESTINATION ($dest). This is exactly how the 909->4-row reversion happened: a truncated backup copied onto the live DB."
                }
            }
        }
        elseif (($low -match 'signals\.db') -and ($low -match 'drop\s+table|delete\s+from|vacuum\s+into')) {
            $flag = $true
            $reason = 'Destructive SQL (DROP TABLE / DELETE FROM / VACUUM INTO) against signals.db.'
        }
    }
}
elseif ($toolName -eq 'Write') {
    $fp = [string]$toolInput.file_path
    if (Test-IsProtectedBasename $fp) {
        $flag = $true
        $reason = "Write tool targeting a protected file ($fp). The live signals.db must never be written as a flat file."
    }
}
elseif ($toolName -eq 'mcp__filesystem__write_file' -or $toolName -eq 'mcp__filesystem__edit_file') {
    $fp = [string]$toolInput.path
    if (Test-IsProtectedBasename $fp) {
        $flag = $true
        $reason = "filesystem MCP writing a protected file ($fp)."
    }
}
elseif ($toolName -eq 'mcp__filesystem__move_file') {
    $dst = [string]$toolInput.destination
    if (Test-IsProtectedBasename $dst) {
        $flag = $true
        $reason = "filesystem MCP moving onto a protected file ($dst)."
    }
}

if (-not $flag) { exit 0 }

$msg = @(
    "[signals.db guard] $reason",
    "",
    "If this is intentional, approve to proceed. To snapshot signals.db first",
    "(hashed + ledgered, non-destructive), run:  /signals-db-checkpoint"
) -join "`n"

$out = [ordered]@{
    hookSpecificOutput = [ordered]@{
        hookEventName            = 'PreToolUse'
        permissionDecision       = 'ask'
        permissionDecisionReason = $msg
    }
}
$out | ConvertTo-Json -Compress -Depth 5 | Write-Output
exit 0
