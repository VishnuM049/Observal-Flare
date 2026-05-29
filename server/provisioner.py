"""Stage-based provisioning pipeline.

Each public function (provision_site, destroy_site, redeploy_site) accepts
injectable dependencies so it works with both real AWS and mock implementations.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import shlex
from datetime import datetime, timezone

import boto3
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from server.compute import AWSCompute, ComputeRunner, MockCompute
from server.config import get_settings
from server.events import publish_site_event
from server.gcp_compute import GCPCompute
from server.gcp_remote import GCPRemoteRunner
from server.gcp_terraform import GCPTerraform
from server.mock import MockGitHubClient, MockSSM, MockTerraform
from server.models.site import Site, SiteStatus, SleepMode
from server.services.github_service import GitHubClient, RealGitHubClient
from server.models.audit_log import AuditLog
from server.services.site_service import audit_details, transition_status
from server.ssm import CommandResult, RealSSM, SSMRunner
from server.terraform import RealTerraform, TerraformRunner

logger = logging.getLogger(__name__)


def _get_defaults(site: Site | None = None) -> tuple[TerraformRunner, SSMRunner, GitHubClient, ComputeRunner]:
    settings = get_settings()
    provider = site.cloud_provider if site else "aws"

    if settings.is_local:
        return MockTerraform(), MockSSM(), MockGitHubClient(), MockCompute()

    gh = RealGitHubClient()

    if provider == "gcp":
        tf = GCPTerraform()
        ssm = GCPRemoteRunner()
        compute = GCPCompute()
    else:
        tf = RealTerraform()
        ssm = RealSSM()
        compute = AWSCompute()

    return tf, ssm, gh, compute


def _generate_env_overrides(site: Site) -> dict[str, str]:
    secret_key = secrets.token_urlsafe(32)

    overrides = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@observal-db:5432/observal",
        "SECRET_KEY": secret_key,
        "CLICKHOUSE_URL": "clickhouse://default:clickhouse@observal-clickhouse:8123/observal",
        "REDIS_URL": "redis://observal-redis:6379",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "postgres",
        "CLICKHOUSE_USER": "default",
        "CLICKHOUSE_PASSWORD": "clickhouse",
    }
    overrides.update(site.env_overrides or {})
    return overrides


def _write_env_script(overrides: dict[str, str]) -> str:
    """Shell snippet: cp .env.example .env, write overrides to a temp file,
    then use Python to merge (override existing keys, append new ones).
    Finally, verify all overrides are present in the resulting .env."""
    overrides_content = "\n".join(f"{k}={v}" for k, v in overrides.items())
    return f"""rm -f .env /tmp/flare-overrides.env
cp .env.example .env
cat > /tmp/flare-overrides.env << 'OVERRIDESEOF'
{overrides_content}
OVERRIDESEOF
python3 -c "
import collections, sys
env = collections.OrderedDict()
for path in ['.env', '/tmp/flare-overrides.env']:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                env[k] = v
with open('.env', 'w') as f:
    for k, v in env.items():
        f.write(f'{{k}}={{v}}\\n')

# Verify all overrides were applied correctly
overrides = {{}}
with open('/tmp/flare-overrides.env') as f:
    for line in f:
        line = line.strip()
        if line and '=' in line:
            k, v = line.split('=', 1)
            overrides[k] = v
missing = []
mismatched = []
for k, expected in overrides.items():
    actual = env.get(k)
    if actual is None:
        missing.append(k)
    elif actual != expected:
        mismatched.append(f'{{k}}: expected [{{expected[:20]}}...] got [{{actual[:20]}}...]')
if missing or mismatched:
    print('WARNING: Some env overrides may not have applied correctly. Consider redeploying after fixing.', file=sys.stderr)
    if missing:
        print(f'  Missing keys: {{missing}}', file=sys.stderr)
    if mismatched:
        for m in mismatched:
            print(f'  Mismatch: {{m}}', file=sys.stderr)
else:
    print(f'ENV OK: {{len(overrides)}} overrides verified')
"
rm -f /tmp/flare-overrides.env"""


async def _validate_credentials(overrides: dict[str, str], cloud_provider: str = "aws") -> list[str]:
    """Validate external credentials before deploy. Returns list of errors (empty = all good)."""
    errors: list[str] = []
    loop = asyncio.get_event_loop()

    if cloud_provider == "gcp":
        try:
            import google.auth
            credentials, project = await loop.run_in_executor(None, google.auth.default)
            settings = get_settings()
            if project and settings.gcp_project_id and project != settings.gcp_project_id:
                errors.append(f"GCP credential project '{project}' does not match configured GCP_PROJECT_ID '{settings.gcp_project_id}'")
        except Exception as e:
            errors.append(f"GCP credentials invalid: {e}")
        return errors

    aws_key = overrides.get("AWS_ACCESS_KEY_ID", "")
    aws_secret = overrides.get("AWS_SECRET_ACCESS_KEY", "")
    aws_region = overrides.get("AWS_REGION", "us-east-1")

    if aws_key and aws_secret:
        try:
            sts = boto3.client(
                "sts",
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                region_name=aws_region,
            )
            await loop.run_in_executor(None, sts.get_caller_identity)
        except Exception as e:
            errors.append(f"AWS credentials invalid: {e}")

    eval_key = overrides.get("EVAL_MODEL_API_KEY", "")
    eval_provider = overrides.get("EVAL_MODEL_PROVIDER", "")
    eval_model = overrides.get("EVAL_MODEL_NAME", "")

    if eval_key and eval_provider == "bedrock" and aws_key and aws_secret:
        try:
            bedrock = boto3.client(
                "bedrock-runtime",
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                region_name=aws_region,
            )
            import json
            body = json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]})
            await loop.run_in_executor(None, lambda: bedrock.invoke_model(modelId=eval_model, body=body))
        except Exception as e:
            err_str = str(e)
            if "AccessDenied" in err_str or "UnrecognizedClient" in err_str:
                errors.append(f"Bedrock access denied for model {eval_model}: {e}")
            elif "ValidationException" in err_str or "ModelNotFound" in err_str:
                errors.append(f"Bedrock model {eval_model} not found or invalid: {e}")
            elif "ThrottlingException" in err_str:
                pass  # throttling means creds are valid, just rate-limited
            else:
                errors.append(f"Bedrock validation failed for {eval_model}: {e}")

    return errors


def _idle_cron_block(site: Site) -> str:
    """Return shell commands that install an idle-detection cron on the instance."""
    if site.sleep_mode != SleepMode.IDLE:
        return ""
    settings = get_settings()
    idle_url = f"{settings.flare_base_url}/api/sites/{site.id}/idle"
    heartbeat_url = f"{settings.flare_base_url}/api/sites/{site.id}/heartbeat"
    threshold_seconds = site.idle_timeout_minutes * 60
    return f"""
# Idle detection + heartbeat: check docker lb logs, report activity, sleep after {site.idle_timeout_minutes}min idle
cat > /opt/observal/idle-check.sh << 'IDLEOF'
#!/bin/bash
THRESHOLD={threshold_seconds}
AUTH="Authorization: Bearer {site.idle_token}"
COMPOSE="docker compose -f /opt/observal/docker/docker-compose.yml -f /opt/observal/docker/docker-compose.production.yml"

# Get recent lb logs within the idle threshold, excluding bots/scanners
LOGS=$($COMPOSE logs observal-lb --since "$((THRESHOLD))s" 2>/dev/null || true)
HUMAN=$(echo "$LOGS" | grep "HTTP/" | grep -viE "bot|crawl|spider|censys|scanner|slurp|semrush|ahrefs|petalsearch|yandex|bingpreview|facebookexternalhit|bytespider|datadog|uptimerobot|pingdom|newrelic|cloudflare|prometheus|grafana|statuscake|site24x7|freshping|kuma" || true)
RECENT=$(echo "$HUMAN" | grep -c "HTTP/" || echo "0")

if [ "$RECENT" -gt 0 ]; then
    # Extract timestamp of the most recent real request
    LAST_TS=$(echo "$HUMAN" | tail -1 | grep -oP '\\[\\K[^]]+' | xargs -I{{}} date -d '{{}}' +%s 2>/dev/null || date +%s)
    curl -sf -X POST -H "$AUTH" -H "Content-Type: application/json" -d '{{"last_request_ts": '"$LAST_TS"'}}' {heartbeat_url} || true
else
    # Site is idle — trigger sleep
    curl -sf -X POST -H "$AUTH" {idle_url} || true
fi
IDLEOF
chmod +x /opt/observal/idle-check.sh
EXISTING=$( (crontab -l 2>/dev/null || true) | (grep -v idle-check || true) )
echo "$EXISTING
*/15 * * * * /opt/observal/idle-check.sh" | crontab -
"""


def _deploy_script(site: Site, sha: str) -> str:
    env_script = _write_env_script(_generate_env_overrides(site))
    return f"""#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/flare-deploy.log) 2>&1

echo "=== Flare deploy for {site.domain} at {sha} ==="

# Wait for startup script to finish (GCP instances run metadata_startup_script on boot)
echo "Waiting for instance startup script to complete..."
for i in $(seq 1 60); do
    [ -f /var/run/flare-startup-complete ] && break
    sleep 5
done
if [ ! -f /var/run/flare-startup-complete ]; then
    echo "WARNING: Startup script marker not found after 5 min, proceeding anyway"
fi

# Install Docker if needed (from official repo — Ubuntu mirrors can be unreliable)
if ! command -v docker &>/dev/null; then
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker && systemctl start docker
fi
if ! command -v docker &>/dev/null; then
    echo "FATAL: Docker could not be installed. Aborting deploy."
    exit 1
fi

# Clone repo
rm -rf /opt/observal
git clone https://github.com/BlazeUp-AI/Observal.git /opt/observal
cd /opt/observal

if [[ "{site.deploy_type.value}" == "pr" ]]; then
    git fetch origin +refs/pull/{shlex.quote(site.deploy_ref)}/head:pr-{shlex.quote(site.deploy_ref)}
    git checkout pr-{shlex.quote(site.deploy_ref)}
else
    git fetch origin {shlex.quote(sha)}
    git checkout {shlex.quote(sha)}
fi

# Write .env: start from .env.example defaults, then apply Flare overrides
{env_script}

# Configure Nginx for this domain
sed -i "s/server_name .*/server_name {site.domain};/" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate .*|ssl_certificate /etc/letsencrypt/live/{site.domain}/fullchain.pem;|" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/letsencrypt/live/{site.domain}/privkey.pem;|" /opt/observal/docker/nginx.production.conf

# TLS cert
if ! [ -d "/etc/letsencrypt/live/{site.domain}" ]; then
    apt-get install -y certbot
    certbot certonly --standalone -d {site.domain} --non-interactive --agree-tos -m {site.requestor_email}
fi

# Build images first (no health check timers running during build)
cd /opt/observal
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml build --jobs 1 || true
# Start services (images are built, containers start fast)
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d || true

# Disable strict mode for auxiliary setup (cron, etc.) — failures here shouldn't kill the deploy
set +euo pipefail

{_idle_cron_block(site)}
echo "=== Deploy complete ==="
"""


async def _wait_for_healthy(site: Site, timeout_seconds: int = 600) -> bool:
    if get_settings().is_local:
        logger.info("[mock] Skipping health check for %s (local mode)", site.domain)
        await asyncio.sleep(2)
        return True

    urls = [f"https://{site.domain}/readyz"]
    if site.ip_address:
        urls.append(f"https://{site.ip_address}/readyz")

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        while asyncio.get_running_loop().time() < deadline:
            for url in urls:
                try:
                    resp = await client.get(url, timeout=10)
                    if resp.status_code == 200:
                        return True
                except Exception:
                    pass
            await asyncio.sleep(15)
    return False


async def provision_site(
    db: AsyncSession,
    site: Site,
    *,
    infra: TerraformRunner | None = None,
    remote: SSMRunner | None = None,
    github: GitHubClient | None = None,
    compute: ComputeRunner | None = None,
) -> Site:
    default_infra, default_remote, default_github, default_compute = _get_defaults(site)
    infra = infra or default_infra
    remote = remote or default_remote
    github = github or default_github
    compute = compute or default_compute

    try:
        # Stage 0: Validate credentials (warn but don't block)
        if not get_settings().is_local:
            overrides = _generate_env_overrides(site)
            await publish_site_event(str(site.id), "stage_progress", message="Validating credentials...")
            cred_errors = await _validate_credentials(overrides, site.cloud_provider)
            if cred_errors:
                warning = f"Credential warning: {'; '.join(cred_errors)}. Deploy will continue but affected features may not work. Consider fixing and redeploying."
                logger.warning(warning)
                site.error_message = warning
                await publish_site_event(str(site.id), "stage_progress", message=warning)

        # Stage 1: Resolve deploy source
        transition_status(site, SiteStatus.PROVISIONING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="provisioning", message="Resolving deploy source...")
        sha = await github.resolve_ref(site.deploy_type.value, site.deploy_ref)
        site.resolved_sha = sha
        await publish_site_event(str(site.id), "stage_progress", message=f"Resolved SHA: {sha[:8]}")

        # Stage 2: Provision infrastructure
        result = await infra.apply(site_name=site.name, instance_size=site.instance_size)
        site.instance_id = result.instance_id
        site.ip_address = result.ip_address
        await db.commit()
        await publish_site_event(str(site.id), "stage_progress", message="Infrastructure provisioned")

        # Stage 3: Wait for instance to be SSH-ready (new instances need time for IAP registration)
        await publish_site_event(str(site.id), "stage_progress", message="Waiting for instance SSH...")
        await asyncio.sleep(30)
        await compute.start(result.instance_id)

        # Stage 4: Deploy application
        transition_status(site, SiteStatus.DEPLOYING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="deploying", message="Deploying application...")
        script = _deploy_script(site, sha)
        cmd_result: CommandResult = await remote.run_command(result.instance_id, script)
        if cmd_result.status != "success":
            logger.warning("Deploy script exited non-zero for %s: %s", site.name, cmd_result.output[:500])
            site.provision_log = cmd_result.output[:2000]

        # Stage 5: Wait for healthy (always runs — script exit code is unreliable)
        # First provision needs longer timeout — Docker build with no cache takes 10-14 min
        await publish_site_event(str(site.id), "stage_progress", message="Waiting for health check...")
        healthy = await _wait_for_healthy(site, timeout_seconds=1200)
        if not healthy:
            script_hint = f" (deploy script also failed: {cmd_result.output[:200]})" if cmd_result.status != "success" else ""
            raise RuntimeError(f"Site {site.domain} did not become healthy within timeout{script_hint}")

        # Stage 6: Success
        transition_status(site, SiteStatus.RUNNING)
        site.last_deployed_at = datetime.now(timezone.utc)
        site.error_message = None
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="running", message="Site is live")

        logger.info("Site %s provisioned successfully", site.domain)

    except Exception as e:
        logger.exception("Provisioning failed for site %s", site.name)
        try:
            site.status = SiteStatus.FAILED
            site.error_message = str(e)[:2000]
            await db.commit()
        except Exception:
            logger.exception("Failed to persist error state for site %s", site.name)
            await db.rollback()
        await publish_site_event(str(site.id), "error", status="failed", message=str(e)[:200])
        raise

    return site


async def destroy_site(
    db: AsyncSession,
    site: Site,
    *,
    infra: TerraformRunner | None = None,
    remote: SSMRunner | None = None,
) -> Site:
    default_infra, default_remote, _, _ = _get_defaults(site)
    infra = infra or default_infra
    remote = remote or default_remote

    try:
        transition_status(site, SiteStatus.DESTROYING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="destroying", message="Destroying site...")

        # Stage 1: Stop application (best-effort — instance may be unreachable)
        if site.instance_id:
            try:
                await remote.run_command(site.instance_id, "cd /opt/observal && docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml down", timeout_seconds=60)
            except Exception:
                logger.warning("Could not stop application on %s (instance may be unreachable)", site.instance_id)

        # Stage 2: Destroy infrastructure (best-effort — state may not exist for failed provisions)
        try:
            await infra.destroy(site_name=site.name)
        except Exception:
            logger.error("Terraform destroy failed for site=%s instance_id=%s — manual cleanup may be required", site.name, site.instance_id, exc_info=True)

        # Stage 3: Clean up Terraform state
        try:
            settings = get_settings()
            if site.cloud_provider == "gcp":
                from google.cloud import storage
                client = storage.Client()
                bucket = client.bucket(settings.gcp_terraform_state_bucket)
                prefix = f"sites/{site.name}/"
                blobs = list(bucket.list_blobs(prefix=prefix))
                for blob in blobs:
                    blob.delete()
            else:
                s3 = boto3.client("s3", region_name=settings.aws_region)
                s3.delete_object(Bucket=settings.terraform_state_bucket, Key=f"sites/{site.name}/terraform.tfstate")
        except Exception:
            logger.warning("Could not clean up Terraform state for %s", site.name)

        # Stage 4: Mark destroyed
        site.status = SiteStatus.DESTROYED
        site.destroyed_at = datetime.now(timezone.utc)
        site.instance_id = None
        site.ip_address = None
        await db.commit()

        db.add(AuditLog(user_id=site.created_by, site_id=site.id, action="site.destroyed", details=audit_details(site)))
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="destroyed", message="Site destroyed")

        logger.info("Site %s destroyed", site.name)

    except Exception as e:
        logger.exception("Destroy failed for site %s", site.name)
        try:
            site.status = SiteStatus.FAILED
            site.error_message = str(e)[:2000]
            await db.commit()
        except Exception:
            logger.exception("Failed to persist error state for site %s", site.name)
            await db.rollback()
        await publish_site_event(str(site.id), "error", status="failed", message=str(e)[:200])
        raise

    return site


async def redeploy_site(
    db: AsyncSession,
    site: Site,
    *,
    infra: TerraformRunner | None = None,
    remote: SSMRunner | None = None,
    github: GitHubClient | None = None,
    compute: ComputeRunner | None = None,
) -> Site:
    _, default_remote, default_github, default_compute = _get_defaults(site)
    remote = remote or default_remote
    github = github or default_github
    compute = compute or default_compute

    try:
        # Stage 0: Validate credentials (warn but don't block)
        if not get_settings().is_local:
            overrides = _generate_env_overrides(site)
            await publish_site_event(str(site.id), "stage_progress", message="Validating credentials...")
            cred_errors = await _validate_credentials(overrides, site.cloud_provider)
            if cred_errors:
                warning = f"Credential warning: {'; '.join(cred_errors)}. Deploy will continue but affected features may not work. Consider fixing and redeploying."
                logger.warning(warning)
                site.error_message = warning
                await publish_site_event(str(site.id), "stage_progress", message=warning)

        # Ensure instance is running (handles sleeping, stopped, failed, or mid-transition states)
        if not get_settings().is_local and site.instance_id:
            state = await compute.get_state(site.instance_id)
            if state != "running":
                await publish_site_event(str(site.id), "stage_progress", message="Starting instance...")
                await compute.start(site.instance_id)
            await remote.run_command(site.instance_id, "cd /opt/observal && docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d --build")
            if site.status in (SiteStatus.SLEEPING, SiteStatus.STOPPED):
                site.status = SiteStatus.RUNNING
                await db.commit()

        # Resolve new SHA
        sha = await github.resolve_ref(site.deploy_type.value, site.deploy_ref)
        site.resolved_sha = sha

        transition_status(site, SiteStatus.DEPLOYING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="deploying", message="Redeploying...")

        # Deploy updated code — retry with volume wipe if init fails (schema migration mismatch)
        env_script = _write_env_script(_generate_env_overrides(site))
        update_script = f"""#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/flare-deploy.log) 2>&1
cd /opt/observal
git fetch origin {shlex.quote(sha)}
git reset --hard {shlex.quote(sha)}

# Write .env: start from .env.example defaults, then apply Flare overrides
{env_script}

# Configure Nginx for this domain
sed -i "s/server_name .*/server_name {site.domain};/" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate .*|ssl_certificate /etc/letsencrypt/live/{site.domain}/fullchain.pem;|" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/letsencrypt/live/{site.domain}/privkey.pem;|" /opt/observal/docker/nginx.production.conf

COMPOSE="docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml"
$COMPOSE up -d --build 2>&1 || true

# Wait for init container to finish (up to 5 min)
for i in $(seq 1 60); do
    if $COMPOSE ps observal-init 2>/dev/null | grep -qE "Exited|exited"; then
        break
    fi
    sleep 5
done

# Check if init container failed (schema migration mismatch)
if $COMPOSE logs observal-init 2>&1 | grep -q "Can't locate revision"; then
{"    echo '=== Migration mismatch detected, wiping data and retrying ==='\n    $COMPOSE down -v\n    $COMPOSE up -d --build" if site.auto_wipe_on_failure else "    echo 'ERROR: Migration mismatch detected. Data preserved. Enable auto-wipe or resolve manually.'\n    exit 1"}
fi

# Restart nginx lb to pick up new container IPs
$COMPOSE restart observal-lb 2>/dev/null || true
"""
        cmd_result = await remote.run_command(site.instance_id, update_script)
        if cmd_result.status != "success":
            raise RuntimeError(f"Redeploy script failed: {cmd_result.output[:500]}")

        # Check health
        healthy = await _wait_for_healthy(site)

        if healthy:
            # Update idle cron config to match current DB settings
            try:
                cron_script = _idle_cron_block(site)
                if cron_script:
                    await remote.run_command(site.instance_id, f"#!/bin/bash\n{cron_script}")
                elif site.sleep_mode != SleepMode.IDLE:
                    await remote.run_command(site.instance_id, '( (crontab -l 2>/dev/null || true) | (grep -v idle-check || true) ) | crontab - ; rm -f /opt/observal/idle-check.sh')
            except Exception:
                logger.warning("Failed to update idle cron on %s (site is healthy, cron may be stale)", site.name)

            transition_status(site, SiteStatus.RUNNING)
            site.last_deployed_at = datetime.now(timezone.utc)
            site.error_message = None
            db.add(AuditLog(user_id=site.created_by, site_id=site.id, action="site.redeployed", details=audit_details(site, resolved_sha=sha)))
            await db.commit()
            await publish_site_event(str(site.id), "status_change", status="running", message="Redeploy complete")
        elif site.auto_wipe_on_failure:
            # Wipe volumes and retry
            logger.warning("Site %s unhealthy after redeploy, wiping volumes", site.name)
            await publish_site_event(str(site.id), "stage_progress", message="Health check failed, wiping data and retrying...")
            wipe_script = """#!/bin/bash
set -euo pipefail
cd /opt/observal
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml down -v
docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d --build
"""
            await remote.run_command(site.instance_id, wipe_script)
            healthy = await _wait_for_healthy(site)
            if healthy:
                transition_status(site, SiteStatus.RUNNING)
                site.last_deployed_at = datetime.now(timezone.utc)
                site.error_message = None
                await db.commit()
                await publish_site_event(str(site.id), "status_change", status="running", message="Redeploy complete (data wiped)")
            else:
                raise RuntimeError("Site unhealthy after wipe and retry")
        else:
            raise RuntimeError("Site unhealthy after redeploy (auto-wipe disabled)")

    except Exception as e:
        logger.exception("Redeploy failed for site %s", site.name)
        try:
            site.status = SiteStatus.FAILED
            site.error_message = str(e)[:2000]
            await db.commit()
        except Exception:
            logger.exception("Failed to persist error state for site %s", site.name)
            await db.rollback()
        await publish_site_event(str(site.id), "error", status="failed", message=str(e)[:200])
        raise

    return site


async def rebuild_site(
    db: AsyncSession,
    site: Site,
    *,
    remote: SSMRunner | None = None,
    compute: ComputeRunner | None = None,
) -> Site:
    """Rebuild containers from existing code — no git pull, just env rewrite + docker compose rebuild."""
    _, default_remote, _, default_compute = _get_defaults(site)
    remote = remote or default_remote
    compute = compute or default_compute

    try:
        # Stage 0: Validate credentials (warn but don't block)
        if not get_settings().is_local:
            overrides = _generate_env_overrides(site)
            await publish_site_event(str(site.id), "stage_progress", message="Validating credentials...")
            cred_errors = await _validate_credentials(overrides, site.cloud_provider)
            if cred_errors:
                warning = f"Credential warning: {'; '.join(cred_errors)}. Rebuild will continue but affected features may not work. Consider fixing and rebuilding."
                logger.warning(warning)
                site.error_message = warning
                await publish_site_event(str(site.id), "stage_progress", message=warning)

        # Ensure instance is running
        if not get_settings().is_local and site.instance_id:
            state = await compute.get_state(site.instance_id)
            if state != "running":
                await publish_site_event(str(site.id), "stage_progress", message="Starting instance...")
                await compute.start(site.instance_id)

        transition_status(site, SiteStatus.DEPLOYING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="deploying", message="Rebuilding...")

        env_script = _write_env_script(_generate_env_overrides(site))
        rebuild_script = f"""#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/flare-deploy.log) 2>&1
cd /opt/observal

# Write .env: start from .env.example defaults, then apply Flare overrides
{env_script}

COMPOSE="docker compose --env-file .env -f docker/docker-compose.yml -f docker/docker-compose.production.yml"
$COMPOSE up -d --build 2>&1

# Wait for init container to finish (up to 5 min)
for i in $(seq 1 60); do
    if $COMPOSE ps observal-init 2>/dev/null | grep -qE "Exited|exited"; then
        break
    fi
    sleep 5
done

# Restart nginx lb to pick up new container IPs
$COMPOSE restart observal-lb 2>/dev/null || true
"""
        cmd_result = await remote.run_command(site.instance_id, rebuild_script)
        if cmd_result.status != "success":
            raise RuntimeError(f"Rebuild script failed: {cmd_result.output[:500]}")

        healthy = await _wait_for_healthy(site)
        if healthy:
            transition_status(site, SiteStatus.RUNNING)
            site.last_deployed_at = datetime.now(timezone.utc)
            site.error_message = None
            db.add(AuditLog(user_id=site.created_by, site_id=site.id, action="site.rebuilt", details=audit_details(site)))
            await db.commit()
            await publish_site_event(str(site.id), "status_change", status="running", message="Rebuild complete")
        else:
            raise RuntimeError("Site unhealthy after rebuild")

    except Exception as e:
        logger.exception("Rebuild failed for site %s", site.name)
        try:
            site.status = SiteStatus.FAILED
            site.error_message = str(e)[:2000]
            await db.commit()
        except Exception:
            logger.exception("Failed to persist error state for site %s", site.name)
            await db.rollback()
        await publish_site_event(str(site.id), "error", status="failed", message=str(e)[:200])
        raise

    return site
