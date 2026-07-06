# Observal Instance Migration Runbook

Migrate all data (registry + telemetry) between Observal instances on EC2.

## Prerequisites

- AWS CLI configured with EC2/SSM permissions
- SSH key pair on Flare instance (`~/.ssh/id_rsa` + `~/.ssh/id_rsa.pub`)
- `observal-cli[migrate]` installed on Flare:
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  pip3 install --break-system-packages 'observal-cli[migrate]'
  ```

## 1. Identify Instances

```bash
# List all running instances
aws ec2 describe-instances --region ap-south-2 --output table \
  --filters "Name=instance-state-name,Values=running,stopped,pending" \
  --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,Type:InstanceType,IP:PublicIpAddress,Site:Tags[?Key==`Site`]|[0].Value}'

# Get Name tags for untagged (non-Flare) instances
aws ec2 describe-instances --region ap-south-2 --output table \
  --instance-ids i-XXXX i-YYYY \
  --query 'Reservations[].Instances[].{ID:InstanceId,IP:PublicIpAddress,Name:Tags[?Key==`Name`]|[0].Value}'

# Confirm which IP a domain points to
dig <subdomain>.observal.io +short
```

## 2. Access Instances

### Flare-deployed instances (have `Site` tag)

SSM works out of the box:
```bash
aws ssm start-session --region ap-south-2 --target i-XXXXXXXXXXXX
```

Note: SSM shell runs as `ssm-user` — use `sudo` for file access.

### Non-Flare instances (no `Site` tag)

SSH via EC2 Instance Connect (keys last 60 seconds):
```bash
aws ec2-instance-connect send-ssh-public-key --region ap-south-2 \
  --instance-id i-XXXXXXXXXXXX \
  --instance-os-user ubuntu \
  --ssh-public-key file://~/.ssh/id_rsa.pub

# Run immediately after:
ssh -i ~/.ssh/id_rsa ubuntu@<PUBLIC_IP>
```

If port 22 is blocked (connection timeout), use SSM if the agent is installed.
Check SSM agent status:
```bash
aws ssm describe-instance-information --region ap-south-2 \
  --filters "Key=InstanceIds,Values=i-XXXXXXXXXXXX" \
  --query 'InstanceInformationList[].{ID:InstanceId,Status:PingStatus}'
```

## 3. Get Database Credentials

Once on the instance, find the .env:
```bash
# Common locations:
cat /home/ubuntu/Observal/.env | grep -iE "POSTGRES|DATABASE|CLICKHOUSE"
cat /opt/observal/.env | grep -iE "POSTGRES|DATABASE|CLICKHOUSE"

# If unsure:
find / -name ".env" -path "*observal*" 2>/dev/null
find /opt -name ".env" 2>/dev/null
find /home -name ".env" 2>/dev/null
```

Confirm DB ports are exposed to localhost:
```bash
docker ps --format "table {{.Names}}\t{{.Ports}}" | grep -iE "postgres|clickhouse|db"
```

Expected output (ports bound to 127.0.0.1):
```
docker-observal-db-1           127.0.0.1:5432->5432/tcp
docker-observal-clickhouse-1   127.0.0.1:8123->8123/tcp
```

Get target org UUID (for import):
```bash
docker exec $(docker ps -qf "name=db") psql -U postgres -d observal \
  -c "SELECT id FROM organizations LIMIT 1;" -t

# If using SSM (need sudo):
sudo docker exec $(sudo docker ps -qf "name=db") psql -U postgres -d observal \
  -c "SELECT id FROM organizations LIMIT 1;" -t
```

## 4. Set Up Tunnels (from Flare)

You need 3 tunnels running in separate terminals.

### Terminal 1: Source instance (SSH tunnel)

```bash
# Push key first (Instance Connect):
aws ec2-instance-connect send-ssh-public-key --region ap-south-2 \
  --instance-id <SOURCE_INSTANCE_ID> \
  --instance-os-user ubuntu \
  --ssh-public-key file://~/.ssh/id_rsa.pub

# Immediately open tunnel:
ssh -L 15432:localhost:5432 -L 18123:localhost:8123 -i ~/.ssh/id_rsa -N ubuntu@<SOURCE_IP>
```

### Terminal 2: Target instance PostgreSQL (SSM port-forward)

```bash
aws ssm start-session --region ap-south-2 --target <TARGET_INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5432"],"localPortNumber":["25432"]}'
```

### Terminal 3: Target instance ClickHouse (SSM port-forward)

```bash
aws ssm start-session --region ap-south-2 --target <TARGET_INSTANCE_ID> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8123"],"localPortNumber":["28123"]}'
```

If the target supports SSH instead of SSM, use SSH tunnels with different local ports:
```bash
ssh -L 25432:localhost:5432 -L 28123:localhost:8123 -i ~/.ssh/id_rsa -N ubuntu@<TARGET_IP>
```

All terminals should sit silent ("Waiting for connections" for SSM, no output for SSH `-N`). That means they're working.

## 5. Run Migration (Terminal 4, on Flare)

```bash
export PATH="$HOME/.local/bin:$PATH"
mkdir -p ~/migration
```

### Phase 1: PostgreSQL (registry data)

```bash
# Export (read-only on source)
observal server migrate export \
  --db-url "postgresql://postgres:<SOURCE_PG_PASS>@localhost:15432/observal" \
  --output ~/migration/export.tar.gz

# Import (idempotent — safe to re-run)
observal server migrate import \
  --db-url "postgresql://postgres:<TARGET_PG_PASS>@localhost:25432/observal" \
  --archive ~/migration/export.tar.gz \
  --org-id <TARGET_ORG_UUID>

# Validate
observal server migrate validate \
  --archive ~/migration/export.tar.gz \
  --db-url "postgresql://postgres:<TARGET_PG_PASS>@localhost:25432/observal"
```

### Phase 2: ClickHouse (telemetry)

```bash
# Export (read-only on source)
observal server migrate export-telemetry \
  --clickhouse-url "clickhouse://default:<SOURCE_CH_PASS>@localhost:18123/observal" \
  --manifest ~/migration/export.manifest.json \
  --output-dir ~/migration/telemetry/

# Import (idempotent + resumable)
observal server migrate import-telemetry \
  --clickhouse-url "clickhouse://default:<TARGET_CH_PASS>@localhost:28123/observal" \
  --input-dir ~/migration/telemetry/ \
  --project-id <TARGET_ORG_UUID>

# Validate
observal server migrate validate-telemetry \
  --input-dir ~/migration/telemetry/ \
  --clickhouse-url "clickhouse://default:<TARGET_CH_PASS>@localhost:28123/observal" \
  --target-db-url "postgresql://postgres:<TARGET_PG_PASS>@localhost:25432/observal"
```

## 6. Expected Warnings (Non-Errors)

| Warning | Meaning | Action |
|---------|---------|--------|
| `Unique conflict in organizations` | Target already has an org with same slug | Safe — existing org preserved |
| `Unique conflict in users` | User with same email already exists on target | Safe — existing user preserved |
| `partition already has data` | Target already has telemetry for that month | Safe — skipped to avoid duplicates |
| `Row count mismatch` (audit_log, security_events) | Skipped partitions cause count difference | Expected if partitions were skipped |
| `orphaned_agent_ids` / `orphaned_user_ids` | Telemetry references IDs that were skipped in PG import | Cosmetic — traces still display |
| `ClickHouse credentials over unencrypted HTTP` | Using localhost tunnel, not public internet | Safe for tunneled connections |

## 7. Cleanup

```bash
# Close all tunnel terminals (Ctrl+C)
# Delete migration artifacts
rm -rf ~/migration/
```

## Port Reference

| Local Port | Tunnels To | Protocol |
|-----------|-----------|----------|
| 15432 | Source PostgreSQL (5432) | SSH |
| 18123 | Source ClickHouse (8123) | SSH |
| 25432 | Target PostgreSQL (5432) | SSM |
| 28123 | Target ClickHouse (8123) | SSM |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Permission denied (publickey)` on SSH | EC2 Instance Connect key expired — re-push with `send-ssh-public-key` then reconnect within 60s |
| `TargetNotConnected` on SSM | Instance doesn't have SSM agent or IAM role — install agent + attach `AmazonSSMManagedInstanceCore` profile |
| `Connection timed out` on SSH | Port 22 blocked in security group — use SSM port-forwarding instead |
| `Not configured` from observal CLI | Run `observal auth login` or `export OBSERVAL_TOKEN=<token>` |
| SSH tunnel drops mid-migration | Re-push key + reconnect tunnel, then re-run the failed command (idempotent) |
