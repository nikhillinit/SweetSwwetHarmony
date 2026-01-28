# SweetSweetHarmony Deployment Guide

This guide covers deploying the Discovery Engine as an always-on appliance for Press On Ventures.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    ALWAYS-ON APPLIANCE                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   Mac Mini / NUC / VPS with:                                │
│   └── SSD storage (50GB+)                                   │
│   └── 8GB+ RAM                                              │
│   └── Wired ethernet (if physical)                          │
│                                                             │
│   Services:                                                 │
│   └── sweetharmony.service (FastAPI on :8000)               │
│   └── sweetharmony-dashboard.service (Streamlit on :8501)   │
│   └── caddy.service (HTTPS reverse proxy on :443)           │
│                                                             │
│   Access:                                                   │
│   └── https://harmony.tailnet-xxxx.ts.net (Tailscale)       │
│   └── or https://harmony.example.com (public domain)        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### Option A: Tailscale Setup (Recommended)

Tailscale provides secure remote access without public ports or DNS configuration.

```bash
# 1. Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# 2. Join your tailnet
sudo tailscale up

# 3. Enable HTTPS certificates
sudo tailscale cert harmony

# 4. Clone and setup
git clone <repo> /opt/sweetharmony
cd /opt/sweetharmony
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 5. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 6. Install services
sudo cp scripts/sweetharmony.service /etc/systemd/system/
sudo cp scripts/sweetharmony-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sweetharmony sweetharmony-dashboard
sudo systemctl start sweetharmony sweetharmony-dashboard

# 7. Install Caddy (optional, for single URL)
sudo apt install caddy
sudo cp scripts/Caddyfile.tailscale /etc/caddy/Caddyfile
# Edit to use your tailnet hostname
sudo systemctl reload caddy

# 8. Verify
curl https://harmony.tailnet-xxxx.ts.net/health
```

### Option B: Public Domain Setup

For access via a public domain with Let's Encrypt certificates.

```bash
# 1-5: Same as Tailscale setup

# 6. Configure DNS
# Point harmony.example.com to your server's public IP

# 7. Install Caddy with production config
sudo apt install caddy
sudo cp scripts/Caddyfile /etc/caddy/Caddyfile
# Edit domain name
sudo systemctl reload caddy

# Caddy will automatically obtain Let's Encrypt certificates
```

### Option C: VPS Deployment

For cloud hosting (DigitalOcean, Linode, etc).

```bash
# 1. Create VPS (Ubuntu 22.04 LTS, 2GB+ RAM)

# 2. Create service user
sudo useradd -r -m -d /opt/sweetharmony sweetharmony

# 3. Clone and setup as sweetharmony user
sudo -u sweetharmony git clone <repo> /opt/sweetharmony
cd /opt/sweetharmony
sudo -u sweetharmony python3 -m venv .venv
sudo -u sweetharmony .venv/bin/pip install -r requirements.txt

# 4. Configure environment
sudo -u sweetharmony cp .env.example .env
sudo nano /opt/sweetharmony/.env

# 5. Create data directory
sudo mkdir -p /opt/sweetharmony/data
sudo chown sweetharmony:sweetharmony /opt/sweetharmony/data

# 6. Install services
sudo cp scripts/sweetharmony.service /etc/systemd/system/
sudo cp scripts/sweetharmony-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sweetharmony sweetharmony-dashboard
sudo systemctl start sweetharmony sweetharmony-dashboard

# 7. Install Caddy
sudo apt install caddy
sudo cp scripts/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile  # Set your domain
sudo systemctl enable caddy
sudo systemctl start caddy
```

## Configuration

### Required Environment Variables

```bash
# Notion Integration
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx

# GitHub Access
GITHUB_TOKEN=ghp_xxx

# LLM Classification
GOOGLE_API_KEY=xxx  # For Gemini

# Database Path
DISCOVERY_DB_PATH=/opt/sweetharmony/data/signals.db

# API Configuration
API_PORT=8000
API_DEBUG=false

# JWT Authentication
JWT_SECRET=<generate with: openssl rand -hex 32>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440  # 24 hours

# Optional: Other collectors
COMPANIES_HOUSE_API_KEY=xxx
PH_API_KEY=xxx
PROXYCURL_API_KEY=xxx
CRUNCHBASE_API_KEY=xxx
```

### Generate JWT Secret

```bash
openssl rand -hex 32
# Add to .env as JWT_SECRET=<output>
```

## Backup & Recovery

### Automated Backups

Set up daily backups with cron:

```bash
# Edit crontab
crontab -e

# Add daily backup at 3 AM
0 3 * * * /opt/sweetharmony/scripts/backup.sh >> /var/log/sweetharmony-backup.log 2>&1

# With S3 upload (optional)
0 3 * * * /opt/sweetharmony/scripts/backup.sh --upload-s3 >> /var/log/sweetharmony-backup.log 2>&1
```

### Manual Backup

```bash
cd /opt/sweetharmony
./scripts/backup.sh
# Creates: /backups/sweetharmony/sweetharmony_YYYYMMDD_HHMMSS.tar.zst
```

### Restore from Backup

```bash
cd /opt/sweetharmony
./scripts/restore.sh /backups/sweetharmony/sweetharmony_20260128_030000.tar.zst
```

## Monitoring

### Service Status

```bash
# Check all services
sudo systemctl status sweetharmony sweetharmony-dashboard caddy

# View logs
sudo journalctl -u sweetharmony -f
sudo journalctl -u sweetharmony-dashboard -f
```

### Health Check

```bash
# API health
curl http://localhost:8000/health

# Detailed health (when implemented)
curl http://localhost:8000/health/detailed
```

### Disk Usage

```bash
# Database sizes
du -h /opt/sweetharmony/data/*.db

# Blob storage
du -sh /opt/sweetharmony/data/blobs/
```

## Security

### Firewall (UFW)

```bash
# Allow SSH
sudo ufw allow ssh

# Allow HTTPS (Caddy handles this)
sudo ufw allow https

# Enable firewall
sudo ufw enable
```

### Fail2Ban (Optional)

```bash
sudo apt install fail2ban

# Create jail for API auth
sudo cat > /etc/fail2ban/jail.d/sweetharmony.conf << 'EOF'
[sweetharmony-auth]
enabled = true
port = https
filter = sweetharmony-auth
logpath = /var/log/caddy/access.log
maxretry = 5
bantime = 600
EOF
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
sudo journalctl -u sweetharmony -n 50

# Common issues:
# - Missing .env file
# - Database permissions
# - Port already in use
```

### Database Locked

```bash
# Check for stuck processes
fuser /opt/sweetharmony/data/signals.db

# Force checkpoint (if needed)
sqlite3 /opt/sweetharmony/data/signals.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

### Certificate Issues (Caddy)

```bash
# Check Caddy logs
sudo journalctl -u caddy -n 50

# Force certificate renewal
sudo caddy reload --config /etc/caddy/Caddyfile
```

## Upgrades

### Standard Upgrade

```bash
cd /opt/sweetharmony
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart sweetharmony sweetharmony-dashboard
```

### Database Migration

Migrations run automatically on startup. To run manually:

```bash
cd /opt/sweetharmony
source .venv/bin/activate
python -c "from storage.signal_store import SignalStore; import asyncio; asyncio.run(SignalStore().initialize())"
```

## Hardware Recommendations

### Minimum (Development)

- Any modern laptop
- 4 GB RAM
- 20 GB SSD
- Wi-Fi acceptable

### Recommended (Production)

- Mac Mini M1/M2 or Intel NUC
- 8-16 GB RAM
- 100+ GB SSD
- Wired ethernet
- UPS for power stability

### With Vector Search (Phase 6+)

- 16+ GB RAM
- 200+ GB SSD
- GPU optional (can use cloud embeddings)
