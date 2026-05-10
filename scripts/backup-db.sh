#!/bin/bash
set -euo pipefail

docker exec flare-db pg_dump -U postgres flare | gzip > /tmp/flare-backup.sql.gz
aws s3 cp /tmp/flare-backup.sql.gz "s3://flare-terraform-state/backups/flare-db-$(date +%Y%m%d).sql.gz"
rm /tmp/flare-backup.sql.gz
