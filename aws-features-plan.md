# AWS Features Plan — Custom AMI, Spot Instances, Data Seeding

These three features require AWS credentials and can't be tested locally. This document captures the full context, implementation plan, and exact files to change so implementation is straightforward once AWS access is available.

---

## 1. Custom AMI

### Problem
Every new site provisions a stock Ubuntu 24.04 AMI, then installs Docker via `apt-get` during the deploy script (`provisioner.py:92-94`). This adds ~2-3 minutes to every provision.

### Solution
Pre-bake an AMI with Docker + Docker Compose already installed. Use it instead of the stock Ubuntu AMI.

### Current state
- `infra/site/main.tf:16-29` — `data.aws_ami.ubuntu` fetches Canonical's stock Ubuntu 24.04
- `provisioner.py:92-94` — deploy script checks `if ! command -v docker` and installs if missing
- `main.tf:38-43` — `user_data` installs SSM agent via snap

### Implementation

**Step 1: Create a Packer template**

Create `infra/packer/flare-site.pkr.hcl`:
```hcl
packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = "~> 1"
    }
  }
}

source "amazon-ebs" "flare_site" {
  ami_name      = "flare-site-{{timestamp}}"
  instance_type = "t3.medium"
  region        = "us-east-1"
  
  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
      virtualization-type = "hvm"
    }
    owners      = ["099720109477"]  # Canonical
    most_recent = true
  }

  ssh_username = "ubuntu"
  
  tags = {
    Name      = "flare-site-base"
    ManagedBy = "flare"
    BuildTime = "{{timestamp}}"
  }
}

build {
  sources = ["source.amazon-ebs.flare_site"]

  provisioner "shell" {
    inline = [
      "sudo apt-get update",
      "sudo apt-get install -y docker.io docker-compose-v2 certbot",
      "sudo systemctl enable docker",
      "sudo snap install amazon-ssm-agent --classic",
      "sudo systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service",
      "sudo apt-get clean",
      "sudo rm -rf /var/lib/apt/lists/*",
    ]
  }
}
```

**Step 2: Build the AMI**
```bash
cd infra/packer
packer init .
packer build flare-site.pkr.hcl
# Output: ami-0abc123...
```

**Step 3: Update Terraform to use the custom AMI**

Add a new variable to `infra/site/variables.tf`:
```hcl
variable "ami_id" {
  description = "Custom AMI ID with Docker pre-installed (empty = use stock Ubuntu)"
  type        = string
  default     = ""
}
```

Change `infra/site/main.tf`:
```hcl
resource "aws_instance" "site" {
  ami = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  # ... rest unchanged
```

Remove the SSM agent `user_data` block (it's in the AMI now).

**Step 4: Pass AMI ID from Flare config**

Add to `server/config.py`:
```python
custom_ami_id: str = ""
```

Add to `.env.example`:
```
CUSTOM_AMI_ID=
```

Update `server/terraform.py` `apply()` to pass the variable:
```python
f"-var=ami_id={get_settings().custom_ami_id}",
```

**Step 5: Simplify the deploy script**

In `provisioner.py:92-94`, the Docker install block becomes a no-op if using the custom AMI. Keep the `if ! command -v docker` guard so it still works with stock AMIs as a fallback. Same for certbot — already installed in the AMI, but the guard is harmless.

### Files to create
- `infra/packer/flare-site.pkr.hcl`

### Files to modify
- `infra/site/variables.tf` — add `ami_id` variable
- `infra/site/main.tf` — use `var.ami_id` with fallback, remove `user_data`
- `server/config.py` — add `custom_ami_id` setting
- `server/terraform.py` — pass `ami_id` to terraform apply
- `.env.example` — add `CUSTOM_AMI_ID`

### Re-baking process
When the base image needs updating (Ubuntu security patches, Docker version bump):
```bash
cd infra/packer && packer build flare-site.pkr.hcl
# Copy the new AMI ID to .env: CUSTOM_AMI_ID=ami-new123
# Existing sites are unaffected — only new provisions use the new AMI
```

---

## 2. Spot Instances

### Problem
PR/branch preview sites are ephemeral (hours to days) but pay on-demand EC2 rates (~$54/mo for t3.large). Spot instances offer the same hardware for ~70% less (~$16/mo).

### Solution
Use spot instances for ephemeral deploy types (PR, branch). Fall back to on-demand if spot capacity is unavailable. Release/tag deploys stay on-demand for stability.

### Current state
- `infra/site/main.tf:31-57` — `aws_instance.site` creates on-demand instances
- `server/terraform.py:71-89` — `apply()` passes `site_name` and `instance_size`
- `server/models/site.py:50` — `deploy_type` enum: branch, commit, pr, tag, release
- `server/services/site_service.py` — `create_site()` sets defaults based on `deploy_type`

### Implementation

**Step 1: Add spot support to Terraform**

Add variable to `infra/site/variables.tf`:
```hcl
variable "use_spot" {
  description = "Use spot instance instead of on-demand"
  type        = bool
  default     = false
}

variable "spot_max_price" {
  description = "Maximum hourly price for spot (empty = on-demand price as cap)"
  type        = string
  default     = ""
}
```

Modify `infra/site/main.tf` — replace `aws_instance.site` with conditional:

```hcl
resource "aws_instance" "site" {
  count = var.use_spot ? 0 : 1
  # ... existing on-demand config
}

resource "aws_spot_instance_request" "site" {
  count                = var.use_spot ? 1 : 0
  ami                  = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type        = var.instance_size
  spot_type            = "one-time"
  wait_for_fulfillment = true
  
  # Same config as on-demand
  subnet_id              = var.subnet_id != "" ? var.subnet_id : null
  vpc_security_group_ids = [aws_security_group.site.id]
  iam_instance_profile   = aws_iam_instance_profile.site.name

  root_block_device {
    volume_size = 50
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name        = "flare-site-${var.site_name}"
    ManagedBy   = "flare"
    Site        = var.site_name
    Environment = "flare"
  }
}

locals {
  instance_id = var.use_spot ? aws_spot_instance_request.site[0].spot_instance_id : aws_instance.site[0].id
}
```

Update `aws_eip.site` to use `local.instance_id`.

Update `infra/site/outputs.tf`:
```hcl
output "instance_id" {
  value = local.instance_id
}
```

**Step 2: Pass use_spot from Flare**

Add to `server/terraform.py` `apply()`:
```python
async def apply(self, site_name: str, instance_size: str, use_spot: bool = False) -> TerraformResult:
    # ...
    f"-var=use_spot={str(use_spot).lower()}",
```

Update `TerraformRunner` abstract method and `MockTerraform` to accept `use_spot`.

**Step 3: Provisioner decides spot vs on-demand**

In `server/provisioner.py`, before calling `infra.apply()`:
```python
use_spot = site.deploy_type in (DeployType.PR, DeployType.BRANCH)
result = await infra.apply(site_name=site.name, instance_size=site.instance_size, use_spot=use_spot)
```

**Step 4: Handle spot unavailability**

If `terraform apply` fails with a spot capacity error, retry with `use_spot=False`:
```python
try:
    result = await infra.apply(site_name=site.name, instance_size=site.instance_size, use_spot=use_spot)
except RuntimeError as e:
    if use_spot and "SpotMaxPriceTooLow" in str(e) or "InsufficientInstanceCapacity" in str(e):
        logger.warning("Spot unavailable for %s, falling back to on-demand", site.name)
        await publish_site_event(str(site.id), "stage_progress", message="Spot unavailable, using on-demand...")
        result = await infra.apply(site_name=site.name, instance_size=site.instance_size, use_spot=False)
    else:
        raise
```

### Files to modify
- `infra/site/variables.tf` — add `use_spot`, `spot_max_price`
- `infra/site/main.tf` — conditional spot vs on-demand resources + locals
- `infra/site/outputs.tf` — use `local.instance_id`
- `server/terraform.py` — add `use_spot` param to `apply()`, `TerraformRunner`, `MockTerraform`
- `server/provisioner.py` — decide spot based on deploy_type, add fallback logic
- `server/mock.py` — update `MockTerraform.apply()` signature

### Spot interruption handling
Spot instances can be reclaimed with 2 min warning. For preview environments this is acceptable — the site goes down temporarily and can be re-provisioned. No special interruption handler needed for MVP. If this becomes a problem, add a CloudWatch event rule that triggers a redeploy on interruption notice.

---

## 3. Data Seeding

### Problem
Newly provisioned sites have an empty database. For demos and PR previews, it's much more useful to start with realistic data (test accounts, sample projects, etc.).

### Solution
Optional DB snapshot that gets restored during deployment. Stored in S3, referenced by config.

### Current state
- `provisioner.py:83-132` — `_deploy_script()` generates the shell script run on EC2
- `server/models/site.py` — Site model, no seed-related fields
- `server/api/sites.py:33-43` — `SiteCreateRequest`, no seed option
- The Observal stack uses PostgreSQL (`observal-db`) and ClickHouse (`observal-clickhouse`)

### Implementation

**Step 1: Create and upload a seed snapshot**

Manually, from an existing Observal instance with good demo data:
```bash
# Dump Observal's Postgres
docker exec observal-db pg_dump -U postgres observal | gzip > observal-seed.sql.gz

# Upload to S3
aws s3 cp observal-seed.sql.gz s3://flare-terraform-state/seeds/observal-seed.sql.gz
```

This is a one-time manual process. Update the seed periodically as the schema evolves.

**Step 2: Add seed option to the site model and API**

Add to `server/models/site.py`:
```python
seed_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Add to `SiteCreateRequest` in `server/api/sites.py`:
```python
seed_snapshot: str | None = None  # S3 key, e.g. "seeds/observal-seed.sql.gz"
```

Add to `SiteUpdateRequest` (not really needed — seed only matters at first deploy).

**Step 3: Add seed config**

Add to `server/config.py`:
```python
seed_s3_bucket: str = "flare-terraform-state"
default_seed_snapshot: str = ""  # e.g. "seeds/observal-seed.sql.gz"
```

Add to `.env.example`:
```
DEFAULT_SEED_SNAPSHOT=
SEED_S3_BUCKET=flare-terraform-state
```

**Step 4: Inject seed restore into the deploy script**

In `provisioner.py`, after `docker compose up -d` and before the health check, add a conditional seed restore block:

```python
def _seed_block(site: Site) -> str:
    if not site.seed_snapshot:
        return ""
    settings = get_settings()
    return f"""
# Seed database from S3 snapshot
echo "=== Restoring seed data ==="
apt-get install -y awscli || true
aws s3 cp s3://{settings.seed_s3_bucket}/{site.seed_snapshot} /tmp/seed.sql.gz
sleep 10  # Wait for Postgres to be ready
gunzip -c /tmp/seed.sql.gz | docker exec -i observal-db psql -U postgres observal
rm -f /tmp/seed.sql.gz
echo "=== Seed restore complete ==="
"""
```

Insert `{_seed_block(site)}` in `_deploy_script()` after the `docker compose up -d` line.

**Step 5: Frontend — add seed option to create form**

Add a checkbox or dropdown to `web/components/site-form.tsx`:
```
[x] Seed with demo data
```

When checked, sends `seed_snapshot: "seeds/observal-seed.sql.gz"` (the default). Could also be a dropdown if multiple snapshots exist.

### Files to create
- Alembic migration for `seed_snapshot` column

### Files to modify
- `server/models/site.py` — add `seed_snapshot` column
- `server/api/sites.py` — add `seed_snapshot` to create/response schemas
- `server/services/site_service.py` — pass `seed_snapshot` through `create_site()`
- `server/config.py` — add `seed_s3_bucket`, `default_seed_snapshot`
- `server/provisioner.py` — add `_seed_block()`, inject into `_deploy_script()`
- `web/components/site-form.tsx` — add seed checkbox
- `web/lib/types.ts` — add `seed_snapshot` to Site and SiteCreateRequest
- `.env.example` — add seed config vars

### Limitations
- Only seeds Postgres, not ClickHouse (analytics data is less important for demos)
- Seed snapshot must match the current schema version — if Observal runs migrations on startup, the seed just needs to be from a compatible version
- No automated seed refresh — manual `pg_dump` + S3 upload when needed
- The EC2 instance needs `awscli` installed (add to custom AMI if using one, or install inline)

---

## Dependency order

1. **Custom AMI first** — spot instances and data seeding both benefit from having Docker + awscli pre-installed
2. **Spot instances second** — standalone, just needs the Terraform change + provisioner fallback
3. **Data seeding last** — needs S3 access + a real Observal instance to dump from

## Prerequisites (all three)
- AWS credentials configured (IAM role with EC2, S3, Route53, SSM permissions)
- S3 bucket `flare-terraform-state` exists
- For custom AMI: Packer installed locally or in CI
- For data seeding: an existing Observal instance with demo-quality data to dump
