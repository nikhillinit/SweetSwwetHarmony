# Cloud Backup — Provisioning Runbook

The Litestream nightly restore-verify workflow
(`.github/workflows/litestream-restore-verify-nightly.yml`) already exists and is
structurally correct. It has been failing because `SQLITE_BACKUP_BUCKET` is empty.

## Prerequisites

- AWS CLI configured with admin credentials, OR Cloudflare R2 account (R2 is cheaper for egress)
- Access to the GitHub repository secrets settings (`Settings → Environments → sqlite-production-backups`)

## Step 1: Provision the S3 bucket (AWS)

```bash
# Replace BUCKET_NAME with your chosen name (e.g., harmonic-signals-backup-prod)
BUCKET_NAME=harmonic-signals-backup-prod
REGION=us-east-1

aws s3api create-bucket \
  --bucket "$BUCKET_NAME" \
  --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

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

# Attach a minimal policy — read/write to the backup bucket only
aws iam put-user-policy \
  --user-name harmonic-litestream \
  --policy-name harmonic-litestream-s3 \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Effect\": \"Allow\",
      \"Action\": [\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\",\"s3:ListBucket\"],
      \"Resource\": [
        \"arn:aws:s3:::$BUCKET_NAME\",
        \"arn:aws:s3:::$BUCKET_NAME/*\"
      ]
    }]
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

Also set repository variable `SQLITE_RESTORE_MIN_SIGNALS` = `100` (fail restore if < 100 rows).

## Step 4: Trigger a manual verify run

After setting secrets, go to:
`Actions → Litestream Restore Verify Nightly → Run workflow`

The first run will fail at "Restore replica" because the bucket is empty (no backups yet).
This is expected. The next Daily Pipeline run will populate the bucket; after that, the nightly verify will pass.

## Step 5: Verify the nightly workflow passes

After the first Daily Pipeline run completes (with Litestream replication enabled — see
`docs/runbooks/cloud-backup-setup.md` for workflow changes), trigger the nightly verify again.
Expected: `scripts.litestream_restore_verify` succeeds with `row_count >= 100`.

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
