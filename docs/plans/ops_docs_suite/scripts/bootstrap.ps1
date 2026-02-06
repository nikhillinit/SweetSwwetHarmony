\
Param(
  [string]$Db = "signals.db"
)

$ErrorActionPreference = "Stop"

Write-Host "== Ops Layer Bootstrap (Windows) =="

if (-not (Test-Path ".\.venv")) {
  Write-Host "Creating virtual environment..."
  py -3.11 -m venv .venv
}

Write-Host "Activating virtual environment..."
. .\.venv\Scripts\Activate.ps1

Write-Host "Upgrading pip..."
python -m pip install --upgrade pip

Write-Host "Installing dependencies..."
pip install -r requirements.txt

# Optional but recommended for .env loading:
pip install python-dotenv | Out-Null

if (-not (Test-Path ".\.env")) {
  if (Test-Path ".\.env.example") {
    Write-Host "Creating .env from template..."
    Copy-Item .env.example .env
    Write-Host "NOTE: Edit .env to add your API key(s) before running extraction."
  } else {
    Write-Host "WARNING: .env.example not found; skipping .env creation."
  }
}

Write-Host "Running bootstrap checks..."
python -m ops.bootstrap --db $Db

Write-Host ""
Write-Host "Next:"
Write-Host "  python -m ops.cli stats --db $Db"
Write-Host "  python -m ops.cli run-extraction --db $Db"
