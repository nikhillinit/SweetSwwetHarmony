# Windows Quickstart (PowerShell)

This guide sets up the ops layer without Docker.

## 0) Prereqs

- Windows 10/11
- Python 3.11+ recommended (3.10+ minimum if your code uses `X | None` typing)
- Git (optional, but typical)
- Network access for `pip install` and your LLM provider

## 1) Create a virtual environment

From your repo root in **PowerShell**:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 2) Install dependencies

```powershell
pip install -r requirements.txt
```

If you plan to use `.env` files, also install:

```powershell
pip install python-dotenv
```

If you want Tenacity-based retries (optional):

```powershell
pip install tenacity
```

## 3) Create your `.env`

Copy the template:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` to include at least one API key.

## 4) Initialize the database

Your storage layer initializes migrations when `OpsStorage` is constructed.

To validate everything quickly:

```powershell
python -m ops.cli stats --db signals.db
```

If you add the optional bootstrap module from this doc set:

```powershell
python -m ops.bootstrap --db signals.db
```

## 5) Run a first extraction pass

```powershell
python -m ops.cli run-extraction --db signals.db
```

Then inspect:

```powershell
python -m ops.cli list --status pending --limit 25 --db signals.db
python -m ops.cli approve 123 --reason "Looks correct" --db signals.db
```

## Common Windows gotchas

### “no such module: fts5”
Your Python build’s SQLite lacks FTS5. Fixes:

- Use an official Python.org build (recommended)
- Upgrade Python (3.11+ tends to work well)
- As a last resort: use an alternate SQLite build (advanced)

### SQLite database in OneDrive / synced folders
Avoid placing `signals.db` in OneDrive/Dropbox-synced directories; file locking and latency can cause odd failures.

### PowerShell execution policy blocks script
If you run the provided `scripts/bootstrap.ps1` and PowerShell blocks it:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

(Only do this if it fits your org’s policies.)
