# GCP Addition + Email Stripping Plan

## Context

Flare currently provisions Observal instances on AWS only (EC2, SSM, Route53, S3, SES). We have unused GCP credits and want to support GCP as an alternative cloud provider. Additionally, the email notification system (SES) is being stripped entirely.

**Goals:**
- Add per-site GCP support (Compute Engine, gcloud SSH, static IPs) alongside existing AWS
- Strip all email/SES code
- Keep existing AWS instances working identically (zero regression)

---

## Decisions

| Question | Decision |
|----------|----------|
| GCP Project | Single dedicated project (all GCE instances are independent within it) |
| DNS | Keep Route53 for all sites (AWS and GCP). GCP Terraform module calls Route53 via AWS provider for DNS records. |
| Authentication | Service account JSON key file. Set `GOOGLE_APPLICATION_CREDENTIALS` in Flare's .env. SA needs: Compute Admin, Service Account User, IAP-Secured Tunnel User, Storage Admin. |
| Domain | Same pattern for both: `{site_name}.observal.io`. No provider-specific subdomains. |
| Terraform state | Separate GCS bucket for GCP sites (`gcp_terraform_state_bucket` in config). AWS stays in S3. |
| Instance sizes | e2-medium, e2-standard-2, e2-standard-4, e2-standard-8 (maps to t3.medium/large/xlarge/2xlarge) |
| gcloud CLI | Install in Dockerfile.api alongside Terraform binary |

---

## Technical Deep Dive: Why Each Change Is Needed

### Why abstract EC2 into ComputeRunner?

`ec2.py` currently exposes bare functions (`start_ec2_instance`, `stop_ec2_instance`) called directly from `worker/tasks.py`. This couples the worker to AWS. With GCP, you need `compute_v1.InstancesClient().start()` instead of `ec2.start_instances()`. Without an abstraction, every call site becomes an `if aws/elif gcp` branch. The `ComputeRunner` interface means call sites just say `compute.start(instance_id)` and the right implementation handles the details.

The pattern already works — `SSMRunner` and `TerraformRunner` prove it. `ec2.py` is the only module that wasn't designed this way from the start.

### Why a separate GCPTerraform class (not conditionals in RealTerraform)?

`RealTerraform` currently configures S3 backend, passes Route53 vars, and reads AWS-specific outputs. Adding GCP inside it means every method gets `if self.provider == "gcp"` branches. A separate `GCPTerraform(TerraformRunner)` class:
- Keeps the AWS path byte-for-byte unchanged
- Each class is focused and testable
- Uses different Terraform modules (`infra/site/` vs `infra/site-gcp/`)

### Why gcloud compute ssh (not startup scripts)?

SSM is fire-and-forget: send a command, poll for result. Startup scripts only run on boot — you can't run arbitrary commands on a running instance. `gcloud compute ssh` with IAP tunneling gives the same capability as SSM:
- Runs commands on any running instance
- No public SSH port needed (IAP tunnels through Google's network at 35.235.240.0/20)
- Returns stdout/stderr like SSM does
- Works with the same `SSMRunner` interface (`run_command(instance_id, script) -> CommandResult`)

### Why strip email instead of abstracting it?

SES is the only AWS service that doesn't have a direct structural equivalent on GCP (no native email service). Rather than integrate SendGrid/Mailgun now, stripping it removes an entire dependency. If email is needed later, a clean implementation can be added without legacy SES coupling.

### Why keep Route53 for GCP sites too?

A domain (`observal.io`) can only have one set of authoritative nameservers. It's currently on Route53. Moving it to Cloud DNS would break all existing AWS site DNS. Keeping Route53 means the GCP Terraform module includes a small AWS provider block just for DNS — one extra config line, zero migration risk.

---

## GCP Setup Prerequisites (before implementation)

1. Create a GCP project (e.g. `flare-observal-prod`)
2. Enable APIs: Compute Engine, IAM, IAP, Cloud Storage
3. Create a service account with roles: `Compute Admin`, `Service Account User`, `IAP-Secured Tunnel User`, `Storage Admin`
4. Download the SA key JSON file
5. Create a GCS bucket for Terraform state (e.g. `flare-terraform-state-gcp`)
6. Enable IAP for the project (APIs & Services > IAP)

---

## Implementation Phases

### Phase 0: Strip Email (Ship immediately, no dependencies)

**Delete:**
- `server/notifications/` (entire directory — `__init__.py`, `email.py`, templates)

**Modify:**
- `server/provisioner.py` — remove `from server.notifications.email import send_site_notification` and all 5 call sites
- `server/worker/tasks.py` — remove import and call in `cron_stale_reminders`
- `server/api/webhooks.py` — remove import and call in `_handle_pull_request`
- `server/config.py` — remove `ses_from_address` field

**No migration needed. No impact on running instances.**

---

### Phase 1: Abstract EC2 into ComputeRunner

**Create:** `server/compute.py`
```python
class ComputeRunner(ABC):
    async def get_state(self, instance_id: str) -> str:
        """Return: 'running', 'stopped', 'stopping', 'pending', 'terminated'"""

    async def start(self, instance_id: str, timeout_seconds: int = 300) -> None:
        """Start instance and wait until ready for commands."""

    async def stop(self, instance_id: str) -> None:
        """Stop instance and wait until fully stopped."""

    async def wait_for_ready(self, instance_id: str, timeout_seconds: int = 180) -> None:
        """Wait until remote execution is available."""

class AWSCompute(ComputeRunner):
    # Wraps existing ec2.py logic (boto3 describe/start/stop + SSM ping wait)

class MockCompute(ComputeRunner):
    # asyncio.sleep(2), returns mock states
```

**Modify:**
- `server/provisioner.py` — add `compute: ComputeRunner | None = None` param to provision/redeploy/rebuild, replace inline `from server.ec2 import` calls
- `server/worker/tasks.py` — replace `from server.ec2 import start_ec2_instance, stop_ec2_instance` with `_get_compute()` helper returning `ComputeRunner`
- `server/config.py` — add `mock_compute: bool | None = None` + `use_mock_compute` property

**Note:** `server/ec2.py` stays alive through Phase 2. Only delete it after Phase 3 (provider routing) is confirmed working. `AWSCompute` wraps `ec2.py` functions initially; the raw file is removed once routing is tested end-to-end.

---

### Phase 2: Add `cloud_provider` to Site Model

**Create:** Migration `server/alembic/versions/0002_add_cloud_provider.py`
```sql
ALTER TABLE sites ADD COLUMN cloud_provider VARCHAR(10) NOT NULL DEFAULT 'aws';
ALTER TABLE sites ALTER COLUMN instance_id TYPE VARCHAR(64);  -- GCE names up to 63 chars
```

**Post-deploy step:** Run migration on the Flare DB after pulling:
```bash
alembic -c server/alembic.ini upgrade head
```
Or manually:
```bash
docker exec flare-db-1 psql -U postgres -d flare -c "ALTER TABLE sites ADD COLUMN cloud_provider VARCHAR(10) NOT NULL DEFAULT 'aws'; ALTER TABLE sites ALTER COLUMN instance_id TYPE VARCHAR(64);"
```
This is non-destructive: ADD COLUMN with DEFAULT doesn't rewrite existing rows, ALTER TYPE widening a varchar is instant. Existing AWS sites get `cloud_provider='aws'` automatically. No data loss, no downtime.

**Modify:**
- `server/models/site.py` — add `CloudProvider` enum (`AWS = "aws"`, `GCP = "gcp"`) + mapped column
- `server/api/sites.py` — add `cloud_provider` to `SiteCreateRequest` and `SiteResponse`
- `server/services/site_service.py` — add `cloud_provider` param to `create_site()`, validate it, adjust pre-flight checks per provider
- `web/lib/types.ts` — add `cloud_provider: "aws" | "gcp"` to `Site` and `SiteCreateRequest`

---

### Phase 3: Provider Routing

**Modify:** `server/provisioner.py`
```python
def _get_defaults(site: Site | None = None):
    provider = site.cloud_provider if site else "aws"
    if provider == "gcp":
        return GCPTerraform(), GCPRemoteRunner(), default_github, GCPCompute()
    return RealTerraform(), RealSSM(), default_github, AWSCompute()
```

**Modify:** `server/worker/tasks.py`
- `_get_remote(site)` → returns `RealSSM()` or `GCPRemoteRunner()` based on `site.cloud_provider`
- `_get_compute(site)` → returns `AWSCompute()` or `GCPCompute()` based on `site.cloud_provider`

**Modify:** `server/config.py` — add:
```python
# GCP
gcp_project_id: str = ""
gcp_region: str = "us-central1"
gcp_zone: str = "us-central1-a"
gcp_terraform_state_bucket: str = ""
```

---

### Phase 4: GCP Terraform Module (parallel with 5+6)

**Create:** `infra/site-gcp/`

`main.tf`:
- `google_compute_instance` — e2 family, 50GB pd-balanced disk, startup script installs Docker
- `google_compute_address` — static external IP

`dns.tf`:
- Uses **AWS provider** for Route53 (keeping DNS on Route53)
- `aws_route53_record` — same pattern as `infra/site/dns.tf`

`firewall.tf`:
- Allow 80/443 from `0.0.0.0/0`
- Allow 22 from `35.235.240.0/20` (IAP tunnel range only)

`iam.tf`:
- `google_service_account` for the instance
- OS Login enabled via instance metadata

`variables.tf`:
- `site_name`, `machine_type`, `zone`, `project`
- `route53_zone_id`, `base_domain` (for DNS via AWS)

`outputs.tf`:
- `instance_name` (stored as `site.instance_id`)
- `public_ip` (stored as `site.ip_address`)
- `zone`

**Create:** `server/gcp_terraform.py` — `GCPTerraform(TerraformRunner)`
- Uses `infra/site-gcp/` module
- GCS backend: `-backend-config=bucket=X -backend-config=prefix=sites/{name}`
- Passes GCP vars + Route53 vars for DNS
- Outputs: maps `instance_name` → `TerraformResult.instance_id`, `public_ip` → `TerraformResult.ip_address`

---

### Phase 5: GCP Remote Runner (parallel with 4+6)

**Create:** `server/gcp_remote.py`
```python
class GCPRemoteRunner(SSMRunner):
    """Execute commands on GCE instances via gcloud compute ssh (IAP tunnel)."""

    def __init__(self, project: str, zone: str):
        self._project = project
        self._zone = zone

    async def run_command(self, instance_id: str, script: str, timeout_seconds: int = 600) -> CommandResult:
        # instance_id = GCE instance name
        # Write script to temp file
        # Run: gcloud compute ssh {instance_id} --tunnel-through-iap
        #      --project={project} --zone={zone} --command="bash -s" < script
        # Capture stdout/stderr via asyncio subprocess
        # Return CommandResult(status, output)
```

Key details:
- Uses `asyncio.create_subprocess_exec` (non-blocking)
- `--tunnel-through-iap` means no public SSH port needed
- `--quiet` suppresses gcloud banners
- Timeout via `asyncio.wait_for`
- Auth via `GOOGLE_APPLICATION_CREDENTIALS` env var (service account key)

---

### Phase 6: GCP Compute Lifecycle (parallel with 4+5)

**Create:** `server/gcp_compute.py`
```python
from google.cloud import compute_v1

class GCPCompute(ComputeRunner):
    def __init__(self, project: str, zone: str):
        self._project = project
        self._zone = zone
        self._client = compute_v1.InstancesClient()

    async def get_state(self, instance_id: str) -> str:
        # instance_id = instance name
        # Call self._client.get(project, zone, instance_name)
        # Map: RUNNING->"running", STOPPED/TERMINATED->"stopped",
        #       STAGING/PROVISIONING->"pending", STOPPING->"stopping"

    async def start(self, instance_id: str, timeout_seconds: int = 300) -> None:
        # self._client.start(project, zone, instance_name)
        # Poll until RUNNING
        # Then wait_for_ready()

    async def stop(self, instance_id: str) -> None:
        # self._client.stop(project, zone, instance_name)
        # Poll until STOPPED/TERMINATED

    async def wait_for_ready(self, instance_id: str, timeout_seconds: int = 180) -> None:
        # Try `gcloud compute ssh --command="echo ready"` until it succeeds
        # This confirms IAP tunnel + OS Login are working
```

---

### Phase 7: Credential Validation for GCP

**Modify:** `server/provisioner.py` `_validate_credentials()`

Add GCP path:
```python
if site.cloud_provider == "gcp":
    try:
        from google.auth import default
        credentials, project = default()
        # Verify project matches config
    except Exception as e:
        errors.append(f"GCP credentials invalid: {e}")
```

**Modify:** `server/services/site_service.py` pre-flight:
```python
if cloud_provider == "gcp":
    if not settings.gcp_project_id:
        missing.append("GCP_PROJECT_ID")
    if not settings.gcp_terraform_state_bucket:
        missing.append("GCP_TERRAFORM_STATE_BUCKET")
```

---

### Phase 8: Destroy Path for GCP

**Modify:** `server/provisioner.py` `destroy_site()`

Route state cleanup — `terraform destroy` removes infrastructure but leaves the state file in the bucket. Must explicitly delete it, same as the S3 pattern for AWS:
```python
if site.cloud_provider == "gcp":
    from google.cloud import storage
    client = storage.Client()
    bucket = client.bucket(settings.gcp_terraform_state_bucket)
    prefix = f"sites/{site.name}/"
    blobs = bucket.list_blobs(prefix=prefix)
    for blob in blobs:
        blob.delete()
else:
    # Existing S3 cleanup
    s3.delete_object(Bucket=settings.terraform_state_bucket, Key=f"sites/{site.name}/terraform.tfstate")
```

---

### Phase 9: Cost Calculations

**Modify:** `server/api/costs.py`

Pricing dicts live at module top-level (easy to grep and update when GCP changes pricing):
```python
# GCP pricing — update periodically from https://cloud.google.com/compute/pricing
GCE_MONTHLY: dict[str, float] = {
    "e2-medium": 24.27,
    "e2-standard-2": 48.54,
    "e2-standard-4": 97.09,
    "e2-standard-8": 194.18,
}
GCE_DISK_MONTHLY = 3.40       # 50GB pd-balanced
GCE_STATIC_IP_STOPPED = 2.88  # unused static IP per month
```

Same pattern as existing AWS pricing (already a top-level dict). Branch `_daily_cost_for_site()` on `site.cloud_provider`.

**Modify:** `web/lib/cost-estimate.ts` — same GCP pricing constants at top of file, branch on provider.

---

### Phase 10: Frontend Updates

**Modify:** `web/components/site-form.tsx`
- Add "Cloud Provider" dropdown (AWS / GCP) at top of form
- Instance size options change based on selected provider:
  - AWS: t3.medium, t3.large, t3.xlarge, t3.2xlarge
  - GCP: e2-medium, e2-standard-2, e2-standard-4, e2-standard-8

**Modify:** `web/components/site-table.tsx`
- Add small provider badge/icon in the table

**Modify:** `web/app/sites/[id]/page.tsx`
- Show cloud provider in site detail info

---

## Dependency Graph

```
Phase 0 (Strip Email) ─────────────────── ship immediately
    |
Phase 1 (ComputeRunner abstraction) ──── ship after Phase 0
    |
Phase 2 (cloud_provider field) ────────── ship after Phase 1
    |
Phase 3 (Provider routing) ────────────── requires Phase 1 + 2
    |
    |── Phase 4 (GCP Terraform) ───────── requires Phase 3
    |── Phase 5 (GCP Remote Runner) ───── requires Phase 3
    |── Phase 6 (GCP Compute) ─────────── requires Phase 3
    |
Phase 7 (Credential validation) ───────── requires Phase 4+5+6
    |
Phase 8 (GCP destroy path) ────────────── requires Phase 4
    |
Phase 9 (Cost calculations) ───────────── requires Phase 2
    |
Phase 10 (Frontend) ───────────────────── requires Phase 2 + 9
```

Phases 4, 5, 6 can be developed in parallel once Phase 3 is done.

---

## Dockerfile Changes

Add to `Dockerfile.api` (after Terraform install):
```dockerfile
# Install gcloud CLI for GCP remote execution
RUN curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts --install-dir=/opt
ENV PATH="/opt/google-cloud-sdk/bin:${PATH}"
```

Add to `requirements` (or pyproject.toml):
```
google-cloud-compute>=1.15.0
google-auth>=2.23.0
```

---

## New .env Fields

```bash
# GCP (leave empty to disable GCP provisioning)
GCP_PROJECT_ID=
GCP_REGION=us-central1
GCP_ZONE=us-central1-a
GCP_TERRAFORM_STATE_BUCKET=
GOOGLE_APPLICATION_CREDENTIALS=  # path to service account JSON key
```

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| `gcloud` CLI adds ~300MB to Docker image | Acceptable; image already ~800MB with Terraform |
| IAP tunnel latency for long deploy scripts | Set 600s timeout (same as SSM); gcloud streams output |
| Service account key file security | Only on Flare server (already secured); can upgrade to Workload Identity Federation later |
| Tests break when `_get_defaults` changes | Default param `site=None` falls back to AWS; all existing tests pass unchanged |
| `instance_id` column too narrow for GCE | Migration widens to VARCHAR(64); safe in Postgres (no rewrite) |
| Route53 in GCP Terraform module (cross-cloud) | Both providers declared in module; AWS creds from Flare's existing config |

---

## Files Summary

**New files:**
- `server/compute.py` — ComputeRunner ABC + AWSCompute
- `server/gcp_terraform.py` — GCPTerraform(TerraformRunner)
- `server/gcp_remote.py` — GCPRemoteRunner(SSMRunner)
- `server/gcp_compute.py` — GCPCompute(ComputeRunner)
- `infra/site-gcp/` — main.tf, dns.tf, firewall.tf, iam.tf, variables.tf, outputs.tf
- `server/alembic/versions/0003_add_cloud_provider.py`

**Modified files:**
- `server/provisioner.py` — inject ComputeRunner, route by provider, strip email
- `server/worker/tasks.py` — use ComputeRunner, route by provider, strip email
- `server/config.py` — add GCP settings, remove SES
- `server/models/site.py` — add cloud_provider field
- `server/api/sites.py` — add to schemas
- `server/services/site_service.py` — validation per provider
- `server/mock.py` — add MockCompute
- `server/api/webhooks.py` — strip email
- `web/lib/types.ts` — add cloud_provider
- `web/components/site-form.tsx` — provider selector + instance sizes
- `web/app/sites/[id]/page.tsx` — show provider
- `server/api/costs.py` — GCP pricing
- `web/lib/cost-estimate.ts` — GCP pricing
- `Dockerfile.api` — add gcloud CLI

**Deleted files:**
- `server/notifications/` (entire directory) — done in Phase 0
- `server/ec2.py` (after Phase 3 confirmed working, logic lives in AWSCompute)

---

## Post-Implementation Setup (on Flare instance)

Once all 10 phases are deployed, SSH into the Flare instance and complete the following:

### 1. GCP Authentication (Workload Identity Federation)

We use Workload Identity Federation (WIF) instead of static SA keys. Flare's EC2 instance
proves its identity to GCP via its AWS IAM role — no long-lived secrets to rotate.

**How it works at runtime:**
```
Flare (on EC2)
  → Gets AWS STS token from EC2 instance metadata (via IAM role)
  → Sends it to GCP STS endpoint
  → GCP validates it against the Workload Identity Pool
  → GCP issues a short-lived access token for flare-provisioner SA
  → Flare uses that token to call Compute Engine / Storage APIs
```

**What the GCP admin set up (already done):**
- GCP project with billing (credits) linked
- APIs enabled: Compute Engine, IAM, IAP, Cloud Storage
- Service account `flare-provisioner` with roles: Compute Admin, Service Account User, IAP-Secured Tunnel User, Storage Admin
- Workload Identity Pool `flare-aws-pool` with AWS provider (our account ID)
- Pool granted access to impersonate `flare-provisioner`
- Credential config JSON downloaded

**What we did on the AWS side (already done):**
- IAM role `flare-ec2-identity` created (no AWS permissions needed, just an identity)
- Role attached to the EC2 instance where Flare runs

**Deploy the credential config on EC2:**

```bash
sudo mkdir -p /etc/flare
sudo nano /etc/flare/gcp-credentials.json
# Paste the WIF credential config JSON (type: "external_account")
```

**Verify from EC2 host:**
```bash
export GOOGLE_APPLICATION_CREDENTIALS=/etc/flare/gcp-credentials.json
gcloud auth login --cred-file=/etc/flare/gcp-credentials.json
gcloud compute instances list --project=<project-id>
```

### 2. `.env` Changes

Add to production `.env`:

```bash
# GCP
GCP_PROJECT_ID=<project-id-from-admin>
GCP_REGION=us-central1
GCP_ZONE=us-central1-a
GCP_TERRAFORM_STATE_BUCKET=<bucket-name-from-admin>
GOOGLE_APPLICATION_CREDENTIALS=/etc/flare/gcp-credentials.json
```

Remove (now dead):

```bash
# SES_FROM_ADDRESS=noreply@observal.io  ← delete this line
```

### 3. GCP Infrastructure Prerequisites

These must exist before Flare can provision GCP sites (confirm with admin):

| Resource | How to verify |
|----------|--------------|
| GCP project with billing | `gcloud projects describe <project-id>` |
| APIs enabled | `gcloud services list --project=<project-id>` |
| GCS state bucket | `gsutil ls gs://<bucket-name>` |
| IAP enabled | Console → APIs & Services → IAP |
| Default VPC network | `gcloud compute networks list --project=<project-id>` |

### 4. Rebuild Docker Image

The Dockerfile and dependencies already include gcloud CLI and google-cloud-* packages. After pulling the latest code:

```bash
docker compose build --no-cache api
```

This rebuilds both `api` and `worker` (same image).

### 5. Docker Compose Volume Mount

The WIF credential config must be accessible inside both `api` and `worker` containers.

In `docker-compose.prod.yml`, change `volumes: []` to:

```yaml
# In both api: and worker: services
volumes:
  - /etc/flare:/etc/flare:ro
```

### 6. Database Migration

```bash
docker compose exec api alembic -c server/alembic.ini upgrade head
```

Runs migration `0002`: adds `cloud_provider` column (defaults existing sites to `"aws"`), widens `instance_id` to VARCHAR(64).

### 7. Existing AWS Setup (unchanged)

Everything AWS still needs what it needed before:
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
- `TERRAFORM_STATE_BUCKET` (S3) + `TERRAFORM_LOCK_TABLE` (DynamoDB)
- `ROUTE53_ZONE_ID` (still used for **both** AWS and GCP sites)

### Setup Checklist

```
[ ] WIF credential config JSON placed at /etc/flare/gcp-credentials.json
[ ] Verified gcloud auth works from EC2 host
[ ] .env updated with GCP_* vars + GOOGLE_APPLICATION_CREDENTIALS
[ ] SES_FROM_ADDRESS removed from .env
[ ] Docker image rebuilt (docker compose build --no-cache api)
[ ] docker-compose.prod.yml updated with /etc/flare volume mount
[ ] alembic upgrade head run
[ ] GCS state bucket exists
[ ] Default VPC network exists in target region
[ ] Route53 zone ID still set (used for GCP DNS too)
[ ] docker compose up — verify api + worker start without import errors
```

### End-to-End Verification

After setup is complete, test the full lifecycle:

```
[ ] Create a GCP site from the UI (select GCP provider, pick e2-standard-2)
[ ] Verify Terraform provisions the GCE instance + static IP + DNS record
[ ] Verify deploy script runs via gcloud SSH (IAP tunnel)
[ ] Verify site comes up healthy at {name}.observal.io
[ ] Stop the site — confirm instance stops
[ ] Start the site — confirm instance starts + containers come back
[ ] Redeploy the site — confirm git pull + rebuild works
[ ] Rebuild the site — confirm env rewrite + container restart works
[ ] Destroy the site — confirm instance deleted + GCS state cleaned up
[ ] Verify Route53 record removed after destroy
[ ] Create an AWS site — confirm zero regression (existing flow unchanged)
```
