# Observal EC2 Site — Terraform Module

Provisions a single EC2 instance ready to run Observal via Docker Compose.

## What it creates

- EC2 instance (Ubuntu 24.04) with Docker, Docker Compose, certbot, git, SSM agent pre-installed
- Elastic IP (static public IP)
- Security group (ports 80, 443 open; SSH optional)
- IAM instance profile (SSM access for remote commands)
- Route53 DNS record (`{site_name}.{base_domain}`)

## Prerequisites

- Terraform >= 1.5
- AWS credentials configured (`aws configure` or env vars)
- An S3 bucket for Terraform state
- A DynamoDB table for state locking (optional but recommended)
- Route53 hosted zone (if you want DNS)

## Usage

### 1. Initialize with backend config

This module uses an S3 backend. You must pass the backend config at init:

```bash
terraform init \
  -backend-config="bucket=YOUR-STATE-BUCKET" \
  -backend-config="key=sites/YOUR-SITE-NAME/terraform.tfstate" \
  -backend-config="region=YOUR-REGION" \
  -backend-config="dynamodb_table=YOUR-LOCK-TABLE"
```

If you don't have an S3 bucket for state, create one first:
```bash
aws s3 mb s3://your-terraform-state-bucket --region us-east-1
```

If you don't want remote state, replace the `backend "s3" {}` block in `main.tf` with:
```hcl
backend "local" {}
```
Then just run `terraform init` with no flags.

### 2. Create terraform.tfvars

```hcl
site_name       = "appian"
instance_size   = "t3.large"
route53_zone_id = "Z1234567890"   # your Route53 zone ID
base_domain     = "yourdomain.io"

# Optional — leave empty for default VPC
vpc_id    = ""
subnet_id = ""

# Optional — CIDR blocks for SSH access (only if you need emergency SSH)
admin_cidr_blocks = []
```

### 3. Set your region

Export the AWS region (the provider block doesn't set it explicitly):

```bash
export AWS_DEFAULT_REGION=us-east-1
```

### 4. Apply

```bash
terraform plan
terraform apply
```

### 5. Deploy Observal

After apply, connect via SSM and deploy:

```bash
# Connect to the instance
aws ssm start-session --target $(terraform output -raw instance_id) --region us-east-1

# On the instance:
sudo -i
git clone https://github.com/Observal/Observal.git /opt/observal
cd /opt/observal
git checkout <your-branch-or-tag>

# Configure .env
cp .env.example .env
# Edit .env — at minimum generate a SECRET_KEY:
#   python3 -c "import secrets; print(secrets.token_hex(32))"
# The defaults work for a single-instance deployment (DB/Redis/ClickHouse all in Docker)

# Configure nginx for your domain (REQUIRED — replace YOUR_DOMAIN below)
sed -i "s/server_name .*/server_name YOUR_DOMAIN;/" docker/nginx.production.conf
sed -i "s|ssl_certificate .*|ssl_certificate /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem;|" docker/nginx.production.conf
sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem;|" docker/nginx.production.conf

# Get TLS cert BEFORE starting containers (certbot needs port 80 free)
# DNS must already point to the instance IP (check: dig YOUR_DOMAIN)
certbot certonly --standalone -d YOUR_DOMAIN --non-interactive --agree-tos -m your@email.com

# Build images first (avoids health check timeout during build)
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml build

# Start services
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d
```

The site should be live at `https://YOUR_DOMAIN` within 1-2 minutes.

Default login (if SEED_DEMO_ACCOUNTS=true in .env):
- Super admin: super@demo.example / super-changeme

### 6. Destroy

```bash
terraform destroy
```

## Outputs

| Output | Description |
|--------|-------------|
| `instance_id` | EC2 instance ID (use with SSM) |
| `public_ip` | Elastic IP address |
| `eip_id` | EIP allocation ID |
| `security_group_id` | Security group ID |
| `dns_fqdn` | Full domain name (if Route53 configured) |

## Notes

- Instance uses SSM for remote access — no SSH keys needed
- Docker is installed from Docker's official apt repo (not Ubuntu's `docker.io`)
- The instance has a 50GB gp3 EBS volume (encrypted)
- User data script runs on first boot and takes 2-3 minutes to complete
