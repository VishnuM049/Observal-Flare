# Flare — Observal Instance Provisioning Platform

## Implementation Plan

> Web tool at `flare.observal.io` for creating, managing, and destroying Observal instances on AWS.
> Internal team uses GitHub OAuth; external clients/prospects get scoped invite links.
> Supports PR deploys, commit-based deploys, env overrides, auto-update on push, and email notifications.

---

## Context: How Observal Deploys Today

Existing instances (dev.observal.io, internal.observal.io) run as **single-node Docker Compose on EC2**.

The deployment process today:
1. EC2 instance exists (manually created or via Terraform)
2. `install-server.sh` downloads a release tarball from GitHub releases
3. `setup.sh` prompts for config → writes `.env`
4. `docker compose up -d` starts: PostgreSQL, ClickHouse, Redis, API (FastAPI/Uvicorn), Web (Next.js), Worker (ARQ), Nginx (TLS termination), Prometheus, Grafana
5. Nginx terminates TLS via Let's Encrypt, routes `/api/*` → port 8000, everything else → port 3000

The repo also has a full **ECS Fargate Terraform module** (`infra/terraform/aws/`) for production-grade deployments with managed RDS, ElastiCache, autoscaling — but current instances don't use it.

### Key Services Per Instance
| Service | Port | Role |
|---------|------|------|
| observal-api | 8000 | FastAPI server |
| observal-web | 3000 | Next.js frontend |
| observal-worker | — | ARQ background worker |
| observal-db | 5432 | PostgreSQL 16 |
| observal-clickhouse | 8123/9000 | ClickHouse analytics |
| observal-redis | 6379 | Redis (cache + queue) |
| observal-lb | 80/443 | Nginx reverse proxy |
| observal-prometheus | 9090 | Metrics |
| observal-grafana | 3001 | Dashboards |

### Docker Images
- API: `ghcr.io/blazeup-ai/observal-api`
- Web: `ghcr.io/blazeup-ai/observal-web`
- Built via multi-stage Dockerfiles in `docker/Dockerfile.api` and `docker/Dockerfile.web`

### Env Vars (from .env.example in Observal repo)
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@observal-db:5432/observal
SECRET_KEY=<generated>
CLICKHOUSE_URL=clickhouse://default:clickhouse@observal-clickhouse:8123/observal
REDIS_URL=redis://observal-redis:6379
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<generated>
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=<generated>
DEPLOYMENT_MODE=local|enterprise
OAUTH_CLIENT_ID=
OAUTH_CLIENT_SECRET=
OAUTH_SERVER_METADATA_URL=
EVAL_MODEL_URL=
EVAL_MODEL_API_KEY=
EVAL_MODEL_NAME=
EVAL_MODEL_PROVIDER=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
```

---

## Architecture Decisions (Confirmed)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Deployment model for sites | Docker Compose on EC2 | Matches current setup, cheap (~$50-100/mo per site), simple, supports PR/commit deploys easily |
| Flare hosting | Dedicated EC2 instance | Internal tool with <10 users, simple and sufficient |
| Domain model | Subdomains of observal.io | e.g., `acme.observal.io`, `pr-42.observal.io` |
| Separate repo | Yes | Different lifecycle, different deploy cadence, not part of the product |
| Tech stack | Next.js + FastAPI + ARQ + PostgreSQL | Team already knows this stack |
| External auth | Invite links (scoped, expiring) | Clients/prospects can spin up demo instances without joining the GitHub org |

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Frontend | Next.js (App Router) + Tailwind + shadcn/ui | Same patterns as Observal web |
| API | FastAPI + Pydantic + SQLAlchemy (async) | Same patterns as observal-server |
| Database | PostgreSQL (on the Flare instance itself) | Stores sites, users, audit logs |
| Task queue | ARQ + Redis | Provisioning jobs are async (5-10 min) |
| Provisioning | Terraform (HCL) | EC2 + security group + DNS per site |
| App deployment | AWS SSM + shell scripts | Clone repo, write .env, docker compose up (no SSH keys) |
| Email | AWS SES (or configurable SMTP) | Notifications |
| Auth | GitHub OAuth + org membership check (internal), invite links (external guests) | Internal team uses GitHub login; clients get scoped invite links |

---

## Data Model

### Site (primary entity)

```python
class Site(Base):
    __tablename__ = "sites"

    id: UUID (PK, default=uuid4)
    name: str (unique, slug — used as subdomain)
    domain: str (computed: f"{name}.observal.io")
    status: Enum [pending, provisioning, deploying, running, stopping, stopped, sleeping, destroying, destroyed, failed]
    requestor_email: str
    created_by: UUID (FK → users.id)
    invite_id: UUID (FK → invites.id, nullable)  # Set if created by a guest via invite
    
    # Deploy source — what code to run
    deploy_type: Enum [branch, commit, pr, tag, release]
    deploy_ref: str  # "main", "abc123", "42", "v0.4.0"
    resolved_sha: str | None  # Actual commit SHA after resolution
    
    # Auto-update
    auto_update: bool (default=False)  # If true, redeploy on new commits to tracked ref
    
    # Redeploy behavior
    auto_wipe_on_failure: bool (default=True for pr/branch, False for release/tag)
    # When true: try redeploy preserving data first. If unhealthy (migration failure etc),
    #   auto-wipe volumes and restart fresh. Notify requestor that data was wiped.
    # When false: never auto-wipe. If redeploy fails, set status=failed and let user decide.
    
    # Sleep/wake
    sleep_mode: Enum [none, nightly, idle]
    # none = never auto-sleep (default for release/tag)
    # nightly = stop containers at 7 PM daily (default for guest/invite sites)
    # idle = stop containers after 2h no traffic (default for internal PR/branch sites)
    
    # Auto-teardown
    scheduled_destroy_at: datetime | None  # Set when PR merges (24h grace period)
    
    # Environment configuration
    env_overrides: JSON  # Key-value pairs merged into .env
    instance_size: str (default="t3.large")
    
    # AWS resources (populated during provisioning)
    instance_id: str | None
    ip_address: str | None
    terraform_state_key: str | None
    
    # Stale site reminders
    ttl_days: int | None  # Default: 1 (24h) for PR/branch, None for release/tag
    reminder_sent_at: datetime | None
    
    # Lifecycle
    created_at: datetime
    updated_at: datetime
    destroyed_at: datetime | None
    last_deployed_at: datetime | None
    
    # Error tracking
    error_message: str | None
    provision_log: Text | None  # Full terraform/deploy output for debugging
```

### User

```python
class User(Base):
    __tablename__ = "users"

    id: UUID (PK)
    email: str (unique)
    name: str
    role: Enum [admin, member, guest]  # admin/member = internal (GitHub OAuth), guest = external (invite link)
    is_active: bool (default=True)
    invite_id: UUID (FK → invites.id, nullable)  # Set for guest users, null for internal
    created_at: datetime
    last_login_at: datetime | None
```

### Invite

```python
class Invite(Base):
    __tablename__ = "invites"

    id: UUID (PK)
    token: str (unique, random 12-char slug)  # Used in URL: flare.observal.io/invite/{token}
    created_by: UUID (FK → users.id)
    label: str | None  # "Acme Corp demo", "YC batch S26" — for your team's reference
    
    # What the guest can do
    max_sites: int (default=1)  # How many sites this invite can create
    allowed_instance_sizes: JSON (default=["t3.large"])  # Lock to specific sizes
    forced_ttl_days: int | None (default=7)  # Sites auto-get this TTL (reminder sent, not auto-destroy)
    allowed_deploy_types: JSON (default=["release", "tag"])  # Restrict to stable refs (no PR/branch access)
    env_overrides_locked: bool (default=True)  # If true, guests can't modify env vars
    
    # Limits
    expires_at: datetime  # Link itself expires
    max_uses: int | None  # None = unlimited people can use this link
    use_count: int (default=0)
    
    created_at: datetime
```

**How invite auth works:**
```
Your team creates an invite in Flare admin →
  Gets URL: flare.observal.io/invite/x7k9m2
  Sends to prospect (email, Slack, etc.)
     │
     ▼
Prospect clicks link →
  Flare validates token (exists? expired? max uses reached?) →
  Shows a simple form: name + email (no GitHub account needed) →
  Creates a User with role=guest, invite_id=invite.id →
  Sets session cookie → redirects to sites dashboard
     │
     ▼
Guest sees only their own sites (scoped by invite) →
  Can create sites within invite limits (max_sites, allowed sizes, forced TTL) →
  Can view, stop, start, destroy their own sites →
  Cannot see internal team sites, other invites, or admin features
```

**What guests CAN do:**
- Create sites (within invite limits)
- View their sites' status, domain, logs
- Stop, start, destroy their own sites
- Access the live Observal instance via the subdomain URL

**What guests CANNOT do:**
- See any other user's sites
- Create sites beyond their invite's limits
- Change instance size beyond allowed options
- Modify env vars (if env_overrides_locked=true)
- Access admin features (create invites, view audit logs, etc.)
- Access webhook or automation features

**When an invite expires or is revoked:**
- Existing guest sessions stay active (they can still manage their running sites)
- Guests cannot create *new* sites (invite limits are checked on site creation)
- Running sites are unaffected (they still cost money — stale site reminders handle cleanup)
- To fully cut off a guest: revoke invite + deactivate their user (sets is_active=false, which blocks all API access)

### AuditLog

```python
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: UUID (PK)
    site_id: UUID (FK → sites.id, nullable)
    user_id: UUID (FK → users.id)
    action: str  # "site.created", "site.destroyed", "site.redeployed"
    details: JSON  # Arbitrary metadata
    created_at: datetime
```

---

## Provisioning Pipeline

### Stage-Based Architecture

Provisioning is a sequence of independent stages. Each stage:
- Receives the Site record + context
- Performs one logical operation
- Updates Site status/fields
- Can fail (sets status=failed + error_message)
- Can be retried individually

```python
PROVISION_STAGES = [
    ResolveDeploySource,    # GitHub API → resolve ref to SHA
    ProvisionInfra,         # Terraform apply → EC2 + DNS
    WaitForInstance,        # Poll EC2 status checks
    DeployApplication,      # SSM → clone, .env, compose up
    WaitForHealthy,         # Poll /readyz (timeout: 10 min)
    SendNotification,       # Email to requestor
]

DESTROY_STAGES = [
    StopApplication,        # SSM → docker compose down (timeout: 60s, skip on failure — instance may be unreachable due to OOM)
    DestroyInfra,           # Terraform destroy (always runs — terminates EC2 + EIP + DNS record at AWS API level, does not need SSM)
    SendDestroyNotification,
]

REDEPLOY_STAGES = [
    WakeIfSleeping,         # If status=sleeping → docker compose start (skipped if already running)
    ResolveDeploySource,    # Get new SHA
    UpdateApplication,      # SSM → pull new code, compose up (try preserving data)
    WaitForHealthy,         # If healthy → done, data preserved
    RetryWithWipe,          # If unhealthy AND auto_wipe_on_failure=true → compose down -v, compose up, wait for healthy again. Notify requestor "data was wiped due to failure"
                            # If unhealthy AND auto_wipe_on_failure=false → set status=failed, let user decide
    SendNotification,
]
```

### Auto-Update via GitHub Webhook

Sites with `auto_update=True` automatically redeploy when new commits are pushed to their tracked branch/PR.

**Setup (one-time):**
A single GitHub webhook is configured on the Observal repo:
- URL: `https://flare.observal.io/api/webhooks/github`
- Events: `push`, `pull_request` (add `issue_comment` later if ChatOps is implemented)
- Secret: shared HMAC secret for signature verification

**Flow:**
```
Developer pushes commit to PR #42
    │
    ▼
GitHub sends POST to flare.observal.io/api/webhooks/github
    payload: { event: "pull_request", action: "synchronize",
               pull_request: { number: 42, head: { sha: "def456" } } }
    │
    ▼
Flare webhook handler:
    1. Verify HMAC signature (reject if invalid)
    2. Query DB: SELECT * FROM sites WHERE deploy_type='pr' AND deploy_ref='42' AND auto_update=true AND status IN ('running', 'sleeping')
    3. For each matching site → enqueue REDEPLOY_STAGES with new SHA
    │
    ▼
Worker executes redeploy (same as manual redeploy)
    │
    ▼
Site now runs the latest commit. No human action required.
```

**Branch tracking works the same way:**
```
Push event to branch "feat/new-ui"
    → Find sites WHERE deploy_type='branch' AND deploy_ref='feat/new-ui' AND auto_update=true AND status IN ('running', 'sleeping')
    → Redeploy each with the new head SHA
```

**Safeguards:**
- Only sites with `auto_update=True` are affected (opt-in per site)
- Webhook signature verification prevents spoofed requests
- **Dedup (latest SHA wins):** If a redeploy is already queued for a site, replace it with the newer SHA (don't stack them). If a redeploy is currently *running*, let it finish, then immediately start one for the latest SHA. This prevents 4 rapid pushes from causing 4 sequential 10-minute deploys — only the latest commit matters. Use `SELECT FOR UPDATE` on the site row when enqueueing to prevent two simultaneous webhooks from creating duplicate jobs.
- Failed auto-redeploys set status to `failed` and email the requestor

### Auto-Teardown on PR Merge/Close (24-hour grace period)

The same webhook receives `pull_request` events with `action: "closed"`.

**Flow:**
```
PR #42 merged or closed
    │
    ▼
Webhook handler:
    1. Find sites WHERE deploy_type='pr' AND deploy_ref='42' AND status IN ('running', 'stopped', 'sleeping')
    2. For each: set scheduled_destroy_at = now() + 24 hours
    3. Send email immediately:
       "PR #42 was merged. Your site pr-42.observal.io will be destroyed in 24 hours.
        [Keep it] [Destroy now]"
    │
    ▼
Cron job (runs hourly):
    1. Find sites WHERE scheduled_destroy_at < now() AND status NOT IN ('destroying', 'destroyed')
    2. For each: enqueue DESTROY_STAGES
```

"Keep it" link in the email clears `scheduled_destroy_at`, preserving the site indefinitely (stale site reminders still apply after TTL days).

### PR Comment with Deploy URL

When a site finishes provisioning (status → running), Flare posts a comment on the associated PR:

```
✅ **Preview environment ready**

| | |
|---|---|
| URL | https://pr-42.observal.io |
| Commit | abc123 |
| Status | Running |

_Auto-updates on push. Will be destroyed 24h after PR close._
```

This is a single GitHub API call:
```
POST /repos/{owner}/{repo}/issues/{pr_number}/comments
{ "body": "✅ Preview ready: https://pr-42.observal.io ..." }
```

Only posted once per site (not on every redeploy). On redeploy, the existing comment is edited with the new SHA.

### Sleep/Wake (Cost Optimization)

Sites can be automatically stopped to save cost via two modes, and manually woken from the Flare UI.

**Model field:** `sleep_mode` on the Site model — `none`, `nightly`, or `idle` (see Data Model section).

**Mode: Nightly (for guest/client sites)**
```
┌──────────────────────────────────────────────┐
│ Nightly shutdown cron (runs at 7 PM daily):   │
│                                               │
│ For each site WHERE status='running'          │
│   AND sleep_mode='nightly':                   │
│                                               │
│   1. SSM → docker compose stop               │
│   2. Set status='sleeping'                    │
│   3. EC2 instance stays alive (keeps EIP)     │
│      but containers are stopped               │
└──────────────────────────────────────────────┘
```
Predictable schedule. Good for demos — clients know the site is available during business hours.

**Mode: Idle (for internal PR/branch sites)**
```
┌──────────────────────────────────────────────┐
│ On the site instance (injected during deploy):│
│                                               │
│ Cron runs every 30 minutes:                   │
│   1. Check nginx access log last modified time│
│   2. If no requests in last 2 hours:          │
│      → POST https://flare.observal.io         │
│        /api/sites/{id}/idle                   │
│                                               │
│ Flare receives the POST:                      │
│   1. SSM → docker compose stop               │
│   2. Set status='sleeping'                    │
└──────────────────────────────────────────────┘
```
The instance reports *itself* as idle — Flare doesn't poll. No SSM rate limit concerns, scales to any number of sites. Injected as a single crontab entry in the deploy script.

**Wake (both modes, manual):**
```
┌──────────────────────────────────────────────┐
│ User clicks "Start" in Flare UI:              │
│   1. SSM → docker compose start              │
│   2. Wait for healthy                          │
│   3. Set status='running'                     │
└──────────────────────────────────────────────┘
```

**Why not auto-wake on HTTP request?** DNS points directly to the EC2 Elastic IP. When containers are stopped, there's nothing to intercept the request — the browser just times out. Auto-wake would require Flare to reverse-proxy all traffic for every site, which is a massive architectural change.

**Cost impact:** A site sleeping ~14 hours/day costs ~$7.60/mo instead of ~$58/mo (only paying for EBS storage + Elastic IP while containers are stopped).

### Stage: ResolveDeploySource

```
Input: deploy_type + deploy_ref
Output: resolved_sha (exact commit)

Logic:
  - branch → GitHub API: GET /repos/{owner}/{repo}/branches/{ref} → commit.sha
  - commit → validate SHA exists, use directly
  - pr → GitHub API: GET /repos/{owner}/{repo}/pulls/{ref} → head.sha
  - tag → GitHub API: GET /repos/{owner}/{repo}/git/ref/tags/{ref} → object.sha
  - release → GitHub API: GET /repos/{owner}/{repo}/releases/tags/{ref} → tarball_url
```

### Stage: ProvisionInfra

Terraform module creates per-site:
```hcl
# infra/site/main.tf

resource "aws_instance" "site" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.instance_size
  subnet_id     = var.subnet_id
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

resource "aws_eip" "site" {
  instance = aws_instance.site.id
}

resource "aws_security_group" "site" {
  # Inbound: 80, 443 from anywhere (or restricted CIDRs)
  # Outbound: all
}

resource "aws_route53_record" "site" {
  zone_id = var.route53_zone_id
  name    = "${var.site_name}.observal.io"
  type    = "A"
  ttl     = 60
  records = [aws_eip.site.public_ip]
}

resource "aws_iam_role" "site" {
  # SSM access for remote commands
}
```

State stored in S3: `s3://flare-terraform-state/sites/{site_name}/terraform.tfstate`

### Stage: DeployApplication

Via AWS SSM (no SSH keys needed). All output is piped to `/var/log/flare-deploy.log` on the instance — SSM truncates output at 24K characters, so on failure Flare uses a second SSM command to `tail -100 /var/log/flare-deploy.log` to get the actual error.

```bash
# Executed on the EC2 instance via SSM SendCommand
# All output: exec > /var/log/flare-deploy.log 2>&1

# 1. Install Docker + Compose (if not in AMI)
# 2. Clone Observal at the resolved SHA
# For PRs: fetch the PR ref directly
# For branches/tags: fetch the specific ref
# For commits: fetch and checkout SHA
git clone https://github.com/BlazeUp-AI/Observal.git /opt/observal
cd /opt/observal
if [[ "${DEPLOY_TYPE}" == "pr" ]]; then
  git fetch origin +refs/pull/${DEPLOY_REF}/head:pr-${DEPLOY_REF}
  git checkout pr-${DEPLOY_REF}
else
  git fetch origin ${RESOLVED_SHA}
  git checkout ${RESOLVED_SHA}
fi

# 3. Write .env from Flare-generated config
cat > /opt/observal/.env << 'ENVEOF'
${generated_env_content}
ENVEOF

# 4. Template Nginx config with site domain
sed -i "s/server_name .*/server_name ${SITE_DOMAIN};/" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate .*|ssl_certificate /etc/letsencrypt/live/${SITE_DOMAIN}/fullchain.pem;|" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/letsencrypt/live/${SITE_DOMAIN}/privkey.pem;|" /opt/observal/docker/nginx.production.conf

# 5. Obtain TLS cert via certbot
certbot certonly --standalone -d ${SITE_DOMAIN} --non-interactive --agree-tos -m admin@observal.io

# 6. Start services
cd /opt/observal
docker compose -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d

# 7. Wait for healthy
for i in $(seq 1 60); do
  curl -sf http://localhost:8000/readyz && exit 0
  sleep 10
done
exit 1
```

### Stage: WaitForHealthy

```python
async def wait_for_healthy(site: Site, timeout_seconds: int = 600):
    url = f"https://{site.domain}/readyz"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = await httpx.get(url, timeout=5)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(15)
    raise ProvisioningError(f"Site {site.domain} did not become healthy within {timeout_seconds}s")
```

---

## API Endpoints

```
# Auth
POST   /api/auth/login          # GitHub OAuth flow (internal users)
POST   /api/auth/logout
GET    /api/auth/me             # Current user (works for both internal + guest)
POST   /api/auth/invite/{token} # Invite redemption (guests — validates token, creates guest user, sets session)

# Sites (guests see only their own; internal users see all)
GET    /api/sites               # List all sites (with status, filters)
POST   /api/sites               # Create site (guests: enforces invite limits)
GET    /api/sites/{id}          # Site detail (status, logs, config)
PATCH  /api/sites/{id}          # Update env_overrides or metadata (guests: blocked if env_overrides_locked)
POST   /api/sites/{id}/redeploy # Pull new code + restart
POST   /api/sites/{id}/stop     # docker compose stop (keep instance)
POST   /api/sites/{id}/start    # docker compose start (from stopped/sleeping)
POST   /api/sites/{id}/destroy  # Terraform destroy (irreversible)
POST   /api/sites/{id}/idle     # Called by the instance itself when no traffic for 2h (triggers sleep)
POST   /api/sites/{id}/unlock   # Force-unlock Terraform state (admin only — for when worker dies mid-apply)
GET    /api/sites/{id}/logs     # Fetch recent docker compose logs via SSM

# Invites (admin only — internal users with role=admin)
GET    /api/invites             # List all invites
POST   /api/invites             # Create invite (set limits, expiry, label)
DELETE /api/invites/{id}        # Revoke invite (existing guest sessions stay active, but no new signups)
GET    /api/invites/{id}/usage  # Who used this invite, what sites they created

GET    /api/health                   # Flare health check (DB + Redis connectivity). Use with an uptime monitor.

GET    /api/deploy-sources/validate  # ?type=pr&ref=42 → validates + returns info

POST   /api/webhooks/github          # GitHub webhook receiver (auto-update)
```

---

## Frontend Pages

```
/login                  → OAuth login (internal users)
/invite/[token]         → Invite landing page (guests — name + email form, no GitHub needed)
/sites                  → Dashboard: table of all sites (guests see only theirs)
/sites/new              → Create form (guests: fields restricted by invite limits)
/sites/[id]             → Detail: status, domain link, config, logs, actions (redeploy/stop/destroy)
/admin/invites          → Invite management (admin only — create, revoke, view usage)
```

### Create Form Fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| Site name | text (slug) | yes | Becomes subdomain. Validated: lowercase, alphanumeric + hyphens |
| Deploy source type | select | yes | branch / commit / pr / tag / release |
| Deploy ref | text | yes | Auto-validated against GitHub API |
| Requestor email | email | yes | Who gets notified |
| Instance size | select | no | Default: t3.large. Options: t3.medium, t3.large, t3.xlarge |
| Env overrides | key-value editor | no | Add/remove env var pairs. Shows defaults for reference |
| Auto-update | checkbox | no | When enabled, site auto-redeploys on new commits to tracked ref |
| Auto-wipe on failure | checkbox | no | Default: checked for PR/branch, unchecked for release/tag. When checked, if a redeploy fails due to migration/startup errors, automatically wipe DB volumes and retry fresh (notifies requestor). When unchecked, fail and let user decide. |
| Deployment mode | select | no | local / enterprise (default: enterprise) |

---

## Repo Structure

```
observal-flare/
├── CLAUDE.md                   # AI assistant context for this repo
├── README.md
├── docker-compose.yml          # Run Flare itself locally
├── docker-compose.prod.yml     # Production overrides for Flare
│
├── web/                        # Next.js frontend
│   ├── package.json
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── (auth)/
│   │   │   └── login/page.tsx
│   │   ├── invite/
│   │   │   └── [token]/page.tsx    # Invite landing (guest signup)
│   │   ├── sites/
│   │   │   ├── page.tsx          # Sites list/dashboard
│   │   │   ├── new/page.tsx      # Create site form
│   │   │   └── [id]/page.tsx     # Site detail
│   │   ├── admin/
│   │   │   └── invites/page.tsx  # Invite management (admin only)
│   │   └── api/                  # Next.js API routes (BFF if needed)
│   ├── components/
│   │   ├── site-form.tsx
│   │   ├── site-table.tsx
│   │   ├── status-badge.tsx
│   │   ├── env-editor.tsx
│   │   ├── invite-form.tsx       # Create invite dialog (admin)
│   │   └── deploy-source-picker.tsx
│   └── lib/
│       ├── api-client.ts
│       └── types.ts
│
├── server/                     # FastAPI backend
│   ├── pyproject.toml
│   ├── main.py                 # App factory
│   ├── config.py               # Settings (pydantic-settings)
│   ├── database.py             # SQLAlchemy async engine
│   ├── models/
│   │   ├── site.py
│   │   ├── user.py
│   │   ├── audit_log.py
│   │   └── invite.py
│   ├── api/
│   │   ├── auth.py             # OAuth + invite redemption + Pydantic schemas
│   │   ├── sites.py            # Site CRUD + actions + Pydantic schemas
│   │   ├── invites.py          # Invite CRUD (admin only)
│   │   ├── deploy_sources.py
│   │   └── webhooks.py         # GitHub webhook receiver (auto-update, auto-teardown)
│   ├── services/
│   │   ├── site_service.py     # Business logic
│   │   ├── invite_service.py   # Invite validation, limit enforcement
│   │   └── github_service.py   # Ref resolution, org membership, PR comments
│   ├── worker/
│   │   ├── tasks.py            # ARQ task definitions
│   │   └── settings.py         # ARQ worker settings
│   ├── provisioner.py          # All provisioning logic: provision, destroy, redeploy as async functions
│   ├── terraform.py            # Terraform CLI wrapper
│   ├── ssm.py                  # AWS SSM command runner
│   ├── notifications/
│   │   ├── email.py
│   │   └── templates/
│   │       ├── site_ready.html
│   │       └── site_failed.html
│   └── alembic/                # DB migrations
│       └── versions/
│
├── infra/
│   ├── flare/                  # Terraform for Flare itself (its own EC2)
│   │   └── main.tf
│   └── site/                   # Terraform module: one Observal site
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       ├── dns.tf              # Route53 A record
│       ├── security_group.tf
│       └── iam.tf
│
├── templates/
│   └── env.template            # .env template with placeholders
│
└── scripts/
    ├── deploy-site.sh          # Shell script run on target EC2 via SSM
    └── setup-flare.sh          # Bootstrap Flare itself
```

---

## Open Questions (resolve before or during implementation)

### 1. DNS Provider — RESOLVED
**Decision**: AWS Route53. Currently on GoDaddy — migration required before Flare can auto-create DNS records.

**Migration plan (one-time, ~30 min active work + 24-48h passive propagation):**
1. AWS Console → Route53 → Create hosted zone for `observal.io` → note the 4 nameserver addresses AWS gives you
2. Open GoDaddy DNS settings side-by-side. Copy every record into Route53:
   - **A records** (name → IP address, e.g., `dev.observal.io → 54.x.x.x`)
   - **CNAME records** (name → name aliases, e.g., `www → observal.io`)
   - **MX records** (email routing — only if `@observal.io` email exists)
   - **TXT records** (domain verification, SPF — copy as-is)
3. In GoDaddy → Domain settings → Nameservers → switch to "Custom" → paste the 4 Route53 nameservers → save
4. Wait 24-48h for propagation (most resolves within 2-6h)

**During propagation**: Existing sites work fine (records exist in both). New Flare sites are accessible by IP but subdomains may not resolve for all users yet. TLS certs (certbot) require DNS to resolve, so new sites use `http://` temporarily. Once propagation completes, everything works automatically.

**Rollback**: If anything goes wrong, flip nameservers back to GoDaddy in GoDaddy's domain settings. Takes effect in minutes.

**Do this before starting Phase 1** — the 48h wait is passive (you're coding during it). By the time you need real deploys, Route53 will be live.

### 2. Auth for Flare Users — RESOLVED
**Decision**: Dual auth — GitHub OAuth for internal team, invite links for external guests.
**Internal users**: "Sign in with GitHub" → verify user is a member of the BlazeUp-AI org → full access (role=admin or member).
**External guests**: Team creates a scoped invite link → guest clicks it, enters name + email → limited access (role=guest, restricted by invite limits: max sites, allowed sizes, forced TTL, locked env vars).
**Setup required**: Create a GitHub OAuth App in the org settings (5 min). Store client ID + secret in Flare's `.env`. No setup needed for invites — they're managed in-app.

### 3. AWS Credentials for Provisioning — RESOLVED
**Decision**: IAM role with role-based access keys.
**How it works**: Create an IAM role with the required permissions (EC2, Route53, SSM, S3, DynamoDB). Generate access keys for the role. Store `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in Flare's `.env`.
**Required IAM permissions**: EC2 (create/terminate instances, manage EIPs, security groups), Route53 (manage records in the observal.io hosted zone), SSM (send commands to Flare-managed instances), S3 (read/write Terraform state bucket), DynamoDB (Terraform lock table).
**Setup**: Create the IAM role + policy in AWS console or via Terraform, generate access keys, add to Flare's `.env`.

### 4. GitHub Token for Flare API Access — RESOLVED
**Decision**: Fine-grained Personal Access Token (PAT).
**Setup**: Generate a fine-grained PAT scoped to the Observal repo in the BlazeUp-AI org. Store as `GITHUB_TOKEN` in Flare's `.env`.
**Required permissions:**
- `contents:read` — resolve branch/PR/tag refs to SHAs
- `members:read` — check org membership
- `pull_requests:write` or `issues:write` — post/edit PR comments
**Note**: Fine-grained PATs have a max expiry of 1 year. Set a calendar reminder to rotate before it expires. If this becomes a pain point, migrate to a GitHub App later (auto-refreshing tokens, no expiry management).

### 5. GitHub Access for Repo Clone (on site instances) — RESOLVED
**Decision**: Everything is public. No auth needed for cloning or image pulls.
**How it works**: `git clone https://github.com/BlazeUp-AI/Observal.git` works without credentials. Docker images on ghcr.io are public. The deploy script needs no tokens or `docker login`.
**Future note**: If enterprise features become private later (private `ee/` directory, private Docker images), revisit this — instances will need a deploy key or token for `docker login ghcr.io`.

### 6. TLS Certificate Strategy — RESOLVED
**Decision**: Let's Encrypt via certbot on each instance (per-site certificates).
**How it works**: During the DeployApplication stage, certbot runs on the EC2 instance to obtain a cert for `{name}.observal.io` via HTTP-01 challenge. Nginx uses it for TLS termination. Matches current Observal setup.
**Rate limit**: Let's Encrypt allows max **50 certificates per registered domain per week**. Unlikely to hit with an internal team, but if site churn grows, upgrade to a wildcard cert (`*.observal.io`) via `certbot-dns-route53` — this is a Phase 3 enhancement.
**Renewal**: Certbot auto-renews via systemd timer on each instance. If a site sleeps for >90 days, run certbot renewal on wake.

### 7. Email Service — RESOLVED
**Decision**: AWS SES.
**Setup required**: One-time domain verification — add DNS records (DKIM + SPF) for observal.io in Route53. Then request production access (moves out of sandbox so you can send to any address). Sandbox approval usually takes 24 hours.
**Env vars**: `SES_FROM_ADDRESS=noreply@observal.io`, `AWS_REGION=us-east-1` (SES uses the same AWS credentials as the rest of Flare).

### 8. Instance AMI — RESOLVED
**Decision**: Ubuntu 24.04 LTS (stock), install Docker at boot.
**How it works**: Terraform uses `data.aws_ami` filtered for Canonical's Ubuntu 24.04 LTS AMI. The deploy script installs Docker + Compose via `apt` (not `yum` — Ubuntu uses apt, not Amazon Linux's package manager).
**Terraform AMI data source:**
```hcl
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]  # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}
```
**Deploy script Docker install** (replaces Amazon Linux yum commands):
```bash
apt-get update && apt-get install -y docker.io docker-compose-v2
systemctl enable docker && systemctl start docker
```
**Optimization (Phase 3)**: Bake a custom AMI with Docker pre-installed to save ~2-3 min per provision.

---

## Implementation Phases

> These phases are ordered by **dependency**, not priority. You'll move through them rapidly.
> Phase 2 requires Phase 1's provisioning pipeline to be working. Phase 3 requires real sites running to tune.

### Phase 1: Core
Everything needed to create, manage, and destroy a site end-to-end.

- [ ] Scaffold repo (Next.js + FastAPI + Docker Compose for Flare itself)
- [ ] Database models + migrations (Site, User, AuditLog, Invite)
- [ ] GitHub OAuth + org membership check (internal users)
- [ ] Invite link system (create invite with limits → guest signs up via link → scoped access)
- [ ] Create Site API + form (all fields: name, deploy source, env overrides, size, auto-update, auto_wipe_on_failure)
- [ ] Terraform module for single EC2 + Elastic IP + security group + DNS record
- [ ] Mock classes (MockTerraform, MockSSM, MockGitHubClient) — build these right after the provisioner interface so all subsequent work is testable locally
- [ ] Provisioning worker (ARQ + Redis): resolve source → terraform apply → deploy app → wait healthy → notify
- [ ] Destroy site (terraform destroy with SSM timeout/skip hardening + force-unlock endpoint)
- [ ] Stop site (docker compose stop, keep instance)
- [ ] Start site (docker compose start from stopped)
- [ ] Redeploy site (try preserving data first; if unhealthy and auto_wipe_on_failure=true, wipe volumes and retry fresh)
- [ ] Sites list page with status badges
- [ ] Site detail page with status, domain link, config, logs, actions
- [ ] Email notifications (site ready / site failed / site destroyed)
- [ ] TLS automation (certbot per instance — obtain cert for {name}.observal.io during deploy)
- [ ] Health check endpoint (GET /api/health — DB + Redis connectivity)
- [ ] Flare DB backup cron (daily pg_dump to S3)

### Phase 2: Automation
All GitHub-driven workflows. Requires Phase 1's provisioning pipeline.
**Prerequisite**: Register the GitHub webhook on the Observal repo (push + pull_request events, pointing to `https://flare.observal.io/api/webhooks/github`).

- [ ] GitHub webhook receiver (HMAC signature verification, event routing)
- [ ] Auto-update on push (match push/PR sync events to tracked sites, enqueue redeploy with dedup)
- [ ] Auto-teardown on PR merge/close (set scheduled_destroy_at = now + 24h, email immediately, hourly cron destroys expired)
- [ ] PR comment with deploy URL (post on site ready, edit on redeploy)
- [ ] Nightly sleep cron (7 PM daily: stop containers on sleep_mode='nightly' sites, set status=sleeping)
- [ ] Idle sleep (inject idle-reporting cron into deploy script, build POST /api/sites/{id}/idle endpoint — sleeps after 2h no traffic for sleep_mode='idle' sites)
- [ ] Stale site reminders (daily cron: find running/sleeping sites where created_at + ttl_days < now, email requestor "still need this site?". Default TTL: 24h for PR/branch, none for release/tag)
- [ ] Spot instances for ephemeral/PR sites (~70% EC2 cost reduction)

### Phase 3: Polish & Optimization
Enhancements that benefit from real usage data and production experience.

- [ ] Real-time status updates (WebSocket or polling for provisioning progress)
- [ ] Log viewer (SSM → docker compose logs → streamed to Flare UI)
- [ ] Audit log page (browse who did what)
- [ ] Site cost estimation display
- [ ] Shareable URL button (copy formatted message with preview link)
- [ ] Wildcard TLS certificate (if hitting Let's Encrypt rate limits — switch to certbot-dns-route53)
- [ ] Custom AMI for faster provisioning (~2-3 min savings)
- [ ] Data seeding (pre-populate DB with snapshot for realistic testing)
- [ ] Bulk operations (destroy all stale sites from dashboard)
- [ ] Shared ALB with SNI routing (cost optimization if running many sites simultaneously)
- [ ] Auto-create on PR label ("deploy-preview" label → create site)
- [ ] ChatOps: /deploy, /destroy, /redeploy, /stop, /start via PR comments (auth via org membership check on commenter)

---

## Testing Per Phase

All tests run against mocks (`FLARE_ENV=local`). Each phase should pass both manual and automated checks before moving to the next.

### Phase 1 Tests

**Manual (click through the UI with mocks running):**
- Sign in with GitHub OAuth → redirects to dashboard
- Sign in as guest via invite link → sees only their own sites
- Create a site → status transitions: pending → provisioning → deploying → running
- View site detail page → shows domain, status, config, action buttons
- Redeploy a site → status goes through redeploy stages, ends at running
- Stop a site → status=stopped, start it → status=running
- Destroy a site → status=destroying → destroyed, disappears from active list
- Create a site as guest → invite limits enforced (max sites, allowed sizes, deploy types)
- Try creating a site with an expired/revoked invite → rejected
- Admin creates and revokes invites from /admin/invites
- Email notifications log to stdout (site ready, failed, destroyed)

**Automated (pytest):**
- `test_provision_pipeline` — provision_site() runs all stages in order, site ends at status=running
- `test_destroy_pipeline` — destroy_site() runs StopApplication (skip on failure) + DestroyInfra, site ends at destroyed
- `test_redeploy_preserves_data` — redeploy succeeds on first try, no wipe triggered
- `test_redeploy_auto_wipe_on_failure` — mock WaitForHealthy to fail once, verify wipe + retry happens when auto_wipe_on_failure=true
- `test_redeploy_no_wipe_when_disabled` — mock WaitForHealthy to fail, verify status=failed when auto_wipe_on_failure=false
- `test_resolve_deploy_source` — each deploy type (branch, commit, pr, tag, release) resolves to a SHA via mock GitHub
- `test_invite_limits` — guest can't exceed max_sites, can't use disallowed instance sizes or deploy types
- `test_invite_expiry` — expired invite token is rejected
- `test_auth_roles` — admin sees all sites, guest sees only their own, unauthenticated gets 401
- `test_site_name_validation` — rejects invalid slugs (uppercase, spaces, duplicates)
- `test_force_unlock` — POST /api/sites/{id}/unlock calls terraform force-unlock

### Phase 2 Tests

**Manual:**
- Send a fake push webhook (via curl or Postman) → matching auto_update site gets redeployed
- Send a fake PR close webhook → site gets scheduled_destroy_at set, email logged
- Wait for hourly cron (or trigger manually) → expired sites get destroyed
- Click "Keep it" link from teardown email → scheduled_destroy_at clears
- Verify sleeping sites wake on auto-update push (status goes sleeping → running)
- Nightly sleep cron runs → sleep_mode=nightly sites go to sleeping
- Idle reporting: simulate POST /api/sites/{id}/idle → site goes to sleeping
- PR comment posted when site finishes provisioning

**Automated (pytest):**
- `test_webhook_signature_verification` — valid HMAC passes, invalid/missing rejects with 401
- `test_auto_update_push` — push event matches branch site with auto_update=true, enqueues redeploy
- `test_auto_update_pr_sync` — PR synchronize event matches PR site, enqueues redeploy
- `test_auto_update_sleeping_site` — sleeping site gets woken then redeployed
- `test_auto_update_ignores_disabled` — site with auto_update=false is not affected
- `test_dedup_latest_sha_wins` — two rapid pushes for same site, only latest SHA is queued (SELECT FOR UPDATE)
- `test_auto_teardown_on_pr_close` — PR close sets scheduled_destroy_at = now + 24h
- `test_teardown_cron_destroys_expired` — sites past scheduled_destroy_at get destroyed
- `test_keep_it_clears_scheduled_destroy` — clearing scheduled_destroy_at preserves the site
- `test_nightly_sleep_cron` — only sleep_mode=nightly + running sites get stopped
- `test_idle_endpoint` — POST /idle on a running sleep_mode=idle site sets status=sleeping
- `test_idle_endpoint_ignored` — POST /idle on sleep_mode=none site does nothing

---

## Key Design Principles

1. **Extensibility over completeness** — Every field, stage, and action is independently modifiable. New requirements = new column + new stage. Never redesign.

2. **Fail loudly, recover gracefully** — Every provisioning stage logs its output. If step 3 fails, steps 1-2 don't need to be re-run. Status tells you exactly where it broke.

3. **Infrastructure as Code, always** — No manual AWS console clicks. Everything through Terraform so it can be destroyed cleanly.

4. **Idempotent operations** — Redeploy can be run any number of times. Terraform apply is naturally idempotent. Deploy scripts use `docker compose up -d` which is idempotent.

5. **Async by default** — The user clicks "Create" and gets immediate feedback. Provisioning happens in the background. Status updates flow to the UI.

---

## Reference: Source Repo Details

| Item | Location |
|------|----------|
| Observal main repo | github.com/BlazeUp-AI/Observal (assumed) |
| Docker Compose (base) | `docker/docker-compose.yml` |
| Docker Compose (prod) | `docker/docker-compose.production.yml` |
| Nginx prod config | `docker/nginx.production.conf` |
| API Dockerfile | `docker/Dockerfile.api` |
| Web Dockerfile | `docker/Dockerfile.web` |
| Entrypoint (migrations) | `docker/entrypoint.sh` |
| Terraform AWS module | `infra/terraform/aws/` |
| Install script | `install-server.sh` |
| Setup script | `docker/server-package/setup.sh` |
| Env template | `docker/server-package/env.template` |
| .env.example | `.env.example` (root) |
| Variables reference | `infra/terraform/aws/variables.tf` |

---

## Terraform State Management

Flare manages many sites, each with its own Terraform state:

```
S3 Bucket: flare-terraform-state
├── sites/
│   ├── acme/terraform.tfstate
│   ├── pr-42/terraform.tfstate
│   └── demo/terraform.tfstate
└── flare/terraform.tfstate        # Flare's own infra
```

DynamoDB table `flare-terraform-locks` prevents concurrent applies to the same site. If the ARQ worker dies mid-apply (OOM, restart), the lock stays engaged. The admin-only `POST /api/sites/{id}/unlock` endpoint runs `terraform force-unlock` to recover.

### Flare DB Backup

Flare's PostgreSQL runs in Docker on a single EC2 instance. If the instance dies, Terraform state is safe in S3, but the site/user/audit mapping is lost. Daily backup to S3:

```bash
# Cron on Flare instance (runs daily at 2 AM):
docker exec flare-db pg_dump -U postgres flare | gzip > /tmp/flare-backup.sql.gz
aws s3 cp /tmp/flare-backup.sql.gz s3://flare-terraform-state/backups/flare-db-$(date +%Y%m%d).sql.gz
rm /tmp/flare-backup.sql.gz
```

Keep 30 days of backups (S3 lifecycle rule deletes older ones).

---

## Security Considerations

- Flare itself must be behind auth — it can create/destroy AWS resources
- IAM role for Flare should be scoped: can only create resources tagged `ManagedBy=flare`
- Terraform state contains secrets (DB passwords) → S3 bucket must be encrypted + access-logged
- Per-site secrets (DB password, SECRET_KEY) are generated fresh per site, never shared
- SSM commands are logged in CloudTrail for audit
- GitHub PAT must be rotated before expiry (max 1 year). Upgrade to a GitHub App for auto-rotating tokens if this becomes a pain point
- Sites should not have SSH key pairs — all access via SSM (no key management)

---

## Cost Estimates

### Per-Site Cost (default: t3.large)

| Resource | Monthly cost |
|----------|-------------|
| t3.large EC2 (2 vCPU, 8GB) | ~$54 |
| 50GB gp3 EBS | ~$4 |
| Elastic IP (while running) | Free |
| Elastic IP (while stopped) | ~$3.60/mo |
| Data transfer (light internal use) | ~$2 |
| **Total per site (running)** | **~$60/mo** |
| **Total per site (stopped/sleeping)** | **~$7.60/mo** |

### Flare Itself

| Resource | Monthly cost |
|----------|-------------|
| t3.small EC2 (Flare app + DB in Docker) | ~$15 |
| 30GB gp3 EBS | ~$2.40 |
| S3 (Terraform state, tiny files) | ~$0.01 |
| SES (low volume emails) | ~$0.10 |
| **Total for Flare** | **~$18/mo** |

### Cost Reduction Strategies

| Strategy | Savings | When to apply |
|----------|---------|---------------|
| Use `t3.medium` for lightweight sites | ~$30/mo per site (drops to ~$30/mo) | When ClickHouse memory can be tuned down to 1GB |
| Stale site reminders (24h default) → team destroys forgotten sites | Eliminates unbounded cost creep | Phase 2 |
| Nightly sleep for client sites (stop containers at 7 PM) | ~87% savings ($7.60/mo instead of $60/mo) | Phase 2 |
| Idle sleep for internal sites (stop after 2h no traffic) | ~87% savings, no manual schedule | Phase 2 |
| Spot instances for PR/branch sites | ~70% off EC2 (~$18 instead of $54) | Phase 2 |

**Note on instance sizing:** The Observal stack (with default memory limits) requires ~4.9GB RAM. `t3.large` (8GB) runs comfortably. `t3.medium` (4GB) requires lowering ClickHouse's memory limit to ~1GB and may be tight under load. `t3.small` (2GB) cannot run the stack.

---

## Relationship to the Observal Repo

**Flare does not import or depend on the Observal codebase.** It is a separate application that *operates on* Observal instances.

**What Flare references from Observal (hardcoded in Flare's deploy scripts/templates):**

| What | Value | Where in Observal |
|------|-------|-------------------|
| Docker compose command | `docker compose -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d` | `docker/` directory |
| Health check URL | `GET /readyz` on port 8000 | `observal-server/api/` |
| Env vars to generate | DATABASE_URL, SECRET_KEY, CLICKHOUSE_URL, REDIS_URL, etc. | `.env.example` |
| Nginx cert paths | `/etc/letsencrypt/live/{domain}/` | `docker/nginx.production.conf` |
| GitHub clone URL | `https://github.com/BlazeUp-AI/Observal.git` | — |

**Do you need the Observal repo in your workspace when building Flare?**

No. The plan document captures everything needed. The Observal repo is only relevant if:
- Observal changes how it deploys (new compose file, renamed health endpoint, new required env var)
- You want to cross-reference the exact `.env.example` when writing the env template

Keep the plan file in the Flare workspace as your reference. Check the Observal repo ad-hoc if a deployment detail needs verification.

---

## Local Development Without AWS (Mocking Strategy)

The provisioner has exactly 3 external boundaries — places where Flare talks to something outside itself. During local development, these are mocked so the entire app works end-to-end on your laptop without AWS credentials.

### External Boundaries

| Boundary | Real behavior | Mocked behavior |
|----------|--------------|-----------------|
| **Terraform** | `subprocess.run(["terraform", "apply", ...])` → creates EC2 | Returns fake instance_id + IP after a 5-second delay |
| **SSM** | `boto3 ssm.send_command(...)` → runs shell on EC2 | Returns success after a 3-second delay |
| **GitHub API** | `httpx.get("api.github.com/repos/.../pulls/42")` → resolves SHA | Returns a hardcoded SHA |

### How It Works in Code

The provisioner functions accept a dependency that's swappable:

```python
# provisioner.py

async def provision_site(site: Site, infra=None, remote=None, github=None):
    infra = infra or RealTerraform()
    remote = remote or RealSSM()
    github = github or RealGitHubClient()

    sha = await github.resolve_ref(site.deploy_type, site.deploy_ref)
    result = await infra.apply(site_name=site.name, instance_size=site.instance_size)
    await remote.run_command(result.instance_id, deploy_script(site, sha))
    ...
```

```python
# mock.py (used in local dev)

class MockTerraform:
    async def apply(self, **kwargs):
        await asyncio.sleep(5)  # Simulate provisioning time
        return TerraformResult(instance_id="i-mock123", ip="127.0.0.1")

    async def destroy(self, **kwargs):
        await asyncio.sleep(3)
        return True

class MockSSM:
    async def run_command(self, instance_id, script):
        await asyncio.sleep(3)
        return CommandResult(status="success", output="mock deploy complete")

class MockGitHubClient:
    async def resolve_ref(self, deploy_type, ref):
        return "abc123deadbeef"  # Fake SHA
```

### What This Lets You Test Locally

- Full UI flow: create site → see status transition pending → provisioning → deploying → running
- Form validation, API error handling, database state transitions
- Worker job pickup and execution
- Webhook receiver (auto-update flow)
- Email rendering (log output instead of SES)
- Dashboard filtering, site detail page, all frontend components

### What It Doesn't Test

- Actual AWS resource creation (needs real credentials)
- Real Terraform state management
- SSM connectivity to a real instance
- DNS propagation and TLS cert issuance
- Docker image builds on the target instance

### Toggle

One env var in Flare's `.env`:

```
FLARE_ENV=local    # Uses mocks — no AWS needed
FLARE_ENV=production  # Uses real Terraform, SSM, GitHub API
```

