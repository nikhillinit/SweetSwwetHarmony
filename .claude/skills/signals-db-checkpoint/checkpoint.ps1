#requires -Version 5.1
<#
  signals-db-checkpoint helper.

  Creates a hashed, ledgered snapshot of the live signals.db before any risky DB
  operation (restore, migration, git reset --hard, bulk DML). Read-only on the live
  DB: it only READS signals.db and WRITES a new backup copy + one ledger line.

  Conventions (matched to the existing restore_db tooling):
    * Backups land in <repo>\backups\ named signals-checkpoint-<UTC>.db
      (the repo's global *.db .gitignore rule keeps them out of version control).
    * The ledger is <repo>\.omx\logs\db_ops_ledger.jsonl, same schema as restore_db
      ({timestamp, pid, tool_name, db_path, action, status, details}).

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File checkpoint.ps1 [-Reason "why"]
#>
param(
    [string]$Reason = ''
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
    if ($env:CLAUDE_PROJECT_DIR -and (Test-Path $env:CLAUDE_PROJECT_DIR)) {
        return (Resolve-Path $env:CLAUDE_PROJECT_DIR).Path
    }
    # $PSScriptRoot is script-scoped and reliable under -File (unlike $MyInvocation
    # inside a function). Script lives at <repo>\.claude\skills\signals-db-checkpoint\.
    if ($PSScriptRoot) {
        $guess = Resolve-Path (Join-Path $PSScriptRoot '..\..\..') -ErrorAction SilentlyContinue
        if ($guess) { return $guess.Path }
    }
    return 'C:\dev\Harmonic'
}

$repo = Resolve-RepoRoot

# Resolve the live DB path (honor DISCOVERY_DB_PATH, default to <repo>\signals.db)
$dbPath = $env:DISCOVERY_DB_PATH
if (-not $dbPath) { $dbPath = Join-Path $repo 'signals.db' }
if (-not [System.IO.Path]::IsPathRooted($dbPath)) { $dbPath = Join-Path $repo $dbPath }

if (-not (Test-Path $dbPath)) {
    Write-Error "signals.db not found at: $dbPath"
    exit 1
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

# Backup destination (repo convention: backups\, *.db is globally gitignored)
$backupDir = Join-Path $repo 'backups'
if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Force -Path $backupDir | Out-Null }
$dest = Join-Path $backupDir "signals-checkpoint-$stamp.db"

# Capture source state BEFORE copy
$hash = (Get-FileHash -Algorithm SHA256 -Path $dbPath).Hash.ToLowerInvariant()
$size = (Get-Item $dbPath).Length

# Row count (best-effort via sqlite3 if available)
$rowCount = $null
try {
    if (Get-Command sqlite3 -ErrorAction SilentlyContinue) {
        $rc = & sqlite3 $dbPath "SELECT count(*) FROM signals;" 2>$null
        if ($LASTEXITCODE -eq 0 -and $rc) { $rowCount = [int]($rc.ToString().Trim()) }
    }
} catch { $rowCount = $null }

# Warn if WAL-resident uncommitted pages exist (snapshot would miss them).
$walWarn = $false
if (Test-Path "$dbPath-wal") {
    $walSize = (Get-Item "$dbPath-wal").Length
    if ($walSize -gt 0) { $walWarn = $true }
}

# Copy the live DB (sidecar-free, matching restore_db convention)
Copy-Item -LiteralPath $dbPath -Destination $dest -Force

$destHash = (Get-FileHash -Algorithm SHA256 -Path $dest).Hash.ToLowerInvariant()
$verifyOk = ($destHash -eq $hash)

# Append to the db-ops ledger (same schema as restore_db)
$ledgerDir = Join-Path $repo '.omx\logs'
if (-not (Test-Path $ledgerDir)) { New-Item -ItemType Directory -Force -Path $ledgerDir | Out-Null }
$ledger = Join-Path $ledgerDir 'db_ops_ledger.jsonl'

$status = if ($verifyOk) { 'success' } else { 'verify_mismatch' }
$entry = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.ffffff+00:00')
    pid       = $PID
    tool_name = 'signals-db-checkpoint'
    db_path   = $dbPath
    action    = 'checkpoint'
    status    = $status
    details   = [ordered]@{
        checkpoint_path = $dest
        src_sha256      = $hash
        dest_sha256     = $destHash
        verify_ok       = $verifyOk
        size_bytes      = $size
        row_count       = $rowCount
        wal_pages_present = $walWarn
        reason          = $Reason
    }
}
Add-Content -Path $ledger -Value ($entry | ConvertTo-Json -Compress -Depth 5) -Encoding utf8

# Human-readable summary
$verifyText = if ($verifyOk) { 'OK (copy matches source)' } else { 'MISMATCH -- investigate before proceeding!' }
Write-Output "signals.db checkpoint created"
Write-Output "  source     : $dbPath"
Write-Output "  checkpoint : $dest"
Write-Output "  sha256     : $hash"
Write-Output "  verify     : $verifyText"
Write-Output ("  size       : {0:N0} bytes" -f $size)
if ($null -ne $rowCount) {
    Write-Output ("  signals    : {0:N0} rows" -f $rowCount)
} else {
    Write-Output "  signals    : (row count unavailable -- sqlite3 not found or 'signals' table absent)"
}
Write-Output "  ledger     : $ledger"
if ($walWarn) {
    Write-Output "  WARNING    : a non-empty signals.db-wal sidecar is present. This snapshot"
    Write-Output "               reflects the committed DB only. For a fully consistent capture,"
    Write-Output "               take the checkpoint when the pipeline is idle, or use restore_db."
}

if (-not $verifyOk) { exit 1 }
exit 0
