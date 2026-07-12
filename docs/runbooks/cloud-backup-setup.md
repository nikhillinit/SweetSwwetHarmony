# Cloud Backup — Provisioning Runbook

The Litestream nightly restore-verify workflow
(`.github/workflows/litestream-restore-verify-nightly.yml`) fails closed by design
until this provisioning is complete (missing `SQLITE_BACKUP_BUCKET` / secrets / seed).
First provisioned live 2026-07-12; the gotchas called out below were all hit during
that run.

## Prerequisites

- AWS CLI configured with admin credentials, OR Cloudflare R2 account (R2 is cheaper for egress)
- Access to the GitHub repository secrets settings (`Settings → Environments → sqlite-production-backups`)

## Step 1: Provision the S3 bucket (AWS)

```bash
# Replace BUCKET_NAME with your chosen name (e.g., harmonic-signals-backup-prod)
BUCKET_NAME=harmonic-signals-backup-prod
REGION=us-east-1

# us-east-1 REJECTS --create-bucket-configuration (InvalidLocationConstraint);
# every other region REQUIRES it. (Hit live 2026-07-12.)
if [ "$REGION" = "us-east-1" ]; then
  aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION"
else
  aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
fi

# Block public access
aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# Enable versioning (protects against accidental overwrites)
aws s3api put-bucket-versioning \
  --bucket "$BUCKET_NAME" \
  --versioning-configuration Status=Enabled

# Lifecycle: delete non-current versions after 30 days
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET_NAME" \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "expire-old-versions",
      "Status": "Enabled",
      "NoncurrentVersionExpiration": {"NoncurrentDays": 30}
    }]
  }'
```

## Step 1 (alternative): Cloudflare R2

R2 has zero egress costs. Create a bucket at `https://dash.cloudflare.com/` → R2 → Create bucket.
Set `endpoint_url` in the Litestream config to `https://<account-id>.r2.cloudflarestorage.com`.
The AWS SDK is compatible with R2.

## Step 2: Create IAM credentials (AWS)

```bash
# Create a dedicated user for Litestream backup
aws iam create-user --user-name harmonic-litestream

# Attach a minimal policy — read/write to the backup bucket only.
# s3:GetBucketLocation is REQUIRED: litestream 0.5.x resolves the bucket region
# via GetBucketLocation before any restore/replicate; without it every workflow
# fails with AccessDenied (hit live 2026-07-12, runs 29205594743/29205620248).
# Bucket-level actions (ListBucket, GetBucketLocation) go on the bucket ARN;
# object-level actions go on the /* ARN — they do nothing on the wrong resource.
aws iam put-user-policy \
  --user-name harmonic-litestream \
  --policy-name harmonic-litestream-s3 \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Effect\": \"Allow\",
        \"Action\": [\"s3:GetBucketLocation\",\"s3:ListBucket\"],
        \"Resource\": \"arn:aws:s3:::$BUCKET_NAME\"
      },
      {
        \"Effect\": \"Allow\",
        \"Action\": [\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\"],
        \"Resource\": \"arn:aws:s3:::$BUCKET_NAME/*\"
      }
    ]
  }"

# Create access key
aws iam create-access-key --user-name harmonic-litestream
# → copy AccessKeyId + SecretAccessKey from output
```

## Step 3: Add secrets to GitHub

Navigate to: `https://github.com/nikhillinit/SweetSwwetHarmony/settings/environments`
→ select (or create) environment `sqlite-production-backups`
→ add secrets:

| Secret name | Value |
|---|---|
| `SQLITE_BACKUP_BUCKET` | `harmonic-signals-backup-prod` (bucket name only, no s3://) |
| `AWS_ACCESS_KEY_ID` | the AccessKeyId from Step 2 |
| `AWS_SECRET_ACCESS_KEY` | the SecretAccessKey from Step 2 |
| `AWS_REGION` | `us-east-1` (or your region) |

Also set environment variable `SQLITE_RESTORE_MIN_SIGNALS` = `500` (fail restore below
500 rows; ratified 2026-07-12 against a verified corpus of 612 — decision 6A: there is
no fallback, the value must be evidence-backed).

## Step 4: Seed the replica (one-time)

The nightly verify and the Daily Pipeline `bootstrap_from_replica` path both restore
from the replica URL — someone has to put a verified snapshot there first. From any
machine with the bucket credentials (AWS CloudShell works — it uses your console
session's own credentials):

```bash
# Snapshot must be pre-verified: integrity ok, expected row count, expected schema
# version, SHA-256 recorded. Never seed an unverified file.
SEED_DB=signals-seed.db   # your verified local snapshot

curl -fsSL \
  "https://github.com/benbjohnson/litestream/releases/download/v0.5.2/litestream-0.5.2-linux-x86_64.tar.gz" \
  -o /tmp/ls.tar.gz
tar -xzf /tmp/ls.tar.gz -C /tmp

# DO NOT use -exec "echo seeded": the subprocess exits in milliseconds and litestream
# shuts down BEFORE uploading anything — restore then fails with "no matching backup
# files available" (hit live 2026-07-12). Give the sync time to complete:
/tmp/litestream replicate -exec "sleep 45" "$SEED_DB" \
  "s3://$BUCKET_NAME/sweetswwetharmony/litestream/signals.db/"

# ALWAYS verify by restoring back from the bucket before trusting the seed:
/tmp/litestream restore -o /tmp/verify.db \
  "s3://$BUCKET_NAME/sweetswwetharmony/litestream/signals.db/"
python3 -c "import sqlite3; c = sqlite3.connect('/tmp/verify.db'); print('integrity:', c.execute('PRAGMA integrity_check').fetchone()[0]); print('signals rows:', c.execute('SELECT COUNT(*) FROM signals').fetchone()[0]); print('schema version:', c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0])"
```

The replica URL must match `discovery-pipeline.yml` and
`litestream-restore-verify-nightly.yml` exactly (`sweetswwetharmony/litestream/signals.db/`).

## Step 5: Trigger a manual verify run

After setting secrets and seeding, go to:
`Actions → Litestream Restore Verify Nightly → Run workflow`

Expected: success on the first run. If the bucket is unseeded the run fails closed by
design ("no matching backup files available") and opens/updates a
`litestream-verify-failure` issue — that issue does NOT auto-close on recovery; close
it manually with a link to the first green run.

## Step 6: Verify the scheduled workflows pass

After the manual verify passes, the recovery-complete evidence requires scheduled runs:
the next scheduled Daily Pipeline plus two consecutive scheduled nightly verifies.
Expected: `scripts.litestream_restore_verify` succeeds with
`signal_count >= SQLITE_RESTORE_MIN_SIGNALS` and `schema_version` at the current value.

## Recovery procedure

To recover the DB from S3:

```bash
BUCKET_NAME=harmonic-signals-backup-prod
REGION=us-east-1
LITESTREAM_VERSION=0.5.2

# Install litestream
curl -sSfL \
  "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.tar.gz" \
  -o /tmp/litestream.tar.gz
tar -xzf /tmp/litestream.tar.gz -C /tmp

# Restore to a temp path first — verify before promoting
/tmp/litestream restore \
  -o /tmp/signals_recovered.db \
  "s3://$BUCKET_NAME/sweetswwetharmony/litestream/signals.db/"

sqlite3 /tmp/signals_recovered.db "PRAGMA integrity_check;"
sqlite3 /tmp/signals_recovered.db "SELECT COUNT(*) FROM signals;"

# Promote only after manual verification
cp /tmp/signals_recovered.db "$DISCOVERY_DB_PATH"
```
