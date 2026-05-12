"""Stage-based provisioning pipeline.

Each public function (provision_site, destroy_site, redeploy_site) accepts
injectable dependencies so it works with both real AWS and mock implementations.
"""
from __future__ import annotations

import logging
import secrets
import shlex
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.events import publish_site_event
from server.mock import MockGitHubClient, MockSSM, MockTerraform
from server.models.site import Site, SiteStatus, SleepMode
from server.notifications.email import send_site_notification
from server.services.github_service import GitHubClient, RealGitHubClient
from server.models.audit_log import AuditLog
from server.services.site_service import audit_details, transition_status
from server.ssm import CommandResult, RealSSM, SSMRunner
from server.terraform import RealTerraform, TerraformRunner

logger = logging.getLogger(__name__)


def _get_defaults() -> tuple[TerraformRunner, SSMRunner, GitHubClient]:
    settings = get_settings()
    tf = MockTerraform() if settings.use_mock_terraform else RealTerraform()
    ssm = MockSSM() if settings.use_mock_ssm else RealSSM()
    gh = MockGitHubClient() if settings.use_mock_github else RealGitHubClient()
    return tf, ssm, gh


def _generate_env(site: Site) -> str:
    secret_key = secrets.token_urlsafe(32)

    base_vars = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@observal-db:5432/observal",
        "SECRET_KEY": secret_key,
        "CLICKHOUSE_URL": "clickhouse://default:clickhouse@observal-clickhouse:8123/observal",
        "REDIS_URL": "redis://observal-redis:6379",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "postgres",
        "CLICKHOUSE_USER": "default",
        "CLICKHOUSE_PASSWORD": "clickhouse",
        "DEPLOYMENT_MODE": "enterprise",
    }
    base_vars.update(site.env_overrides or {})
    return "\n".join(f"{k}={v}" for k, v in base_vars.items())


def _idle_cron_block(site: Site) -> str:
    """Return shell commands that install an idle-detection cron on the instance."""
    if site.sleep_mode != SleepMode.IDLE:
        return ""
    settings = get_settings()
    callback_url = f"{settings.flare_base_url}/api/sites/{site.id}/idle"
    threshold_seconds = site.idle_timeout_minutes * 60
    return f"""
# Idle detection: check nginx access log, sleep after {site.idle_timeout_minutes}min idle
cat > /opt/observal/idle-check.sh << 'IDLEOF'
#!/bin/bash
LOG="/var/log/nginx/access.log"
THRESHOLD={threshold_seconds}
if [ ! -f "$LOG" ]; then exit 0; fi
LAST_MOD=$(stat -c %Y "$LOG" 2>/dev/null || stat -f %m "$LOG" 2>/dev/null)
NOW=$(date +%s)
AGE=$(( NOW - LAST_MOD ))
if [ "$AGE" -ge "$THRESHOLD" ]; then
    curl -sf -X POST -H "Authorization: Bearer {site.idle_token}" {callback_url} || true
fi
IDLEOF
chmod +x /opt/observal/idle-check.sh
(crontab -l 2>/dev/null | grep -v idle-check; echo "*/30 * * * * /opt/observal/idle-check.sh") | crontab -
"""


def _deploy_script(site: Site, sha: str) -> str:
    env_content = _generate_env(site)
    return f"""#!/bin/bash
set -euo pipefail
exec > /var/log/flare-deploy.log 2>&1

echo "=== Flare deploy for {site.domain} at {sha} ==="

# Install Docker if needed
if ! command -v docker &>/dev/null; then
    apt-get update && apt-get install -y docker.io docker-compose-v2
    systemctl enable docker && systemctl start docker
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

# Write .env
cat > /opt/observal/.env << 'ENVEOF'
{env_content}
ENVEOF

# Configure Nginx for this domain
sed -i "s/server_name .*/server_name {site.domain};/" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate .*|ssl_certificate /etc/letsencrypt/live/{site.domain}/fullchain.pem;|" /opt/observal/docker/nginx.production.conf
sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/letsencrypt/live/{site.domain}/privkey.pem;|" /opt/observal/docker/nginx.production.conf

# TLS cert
if ! [ -d "/etc/letsencrypt/live/{site.domain}" ]; then
    apt-get install -y certbot
    certbot certonly --standalone -d {site.domain} --non-interactive --agree-tos -m admin@observal.io
fi

# Start services
cd /opt/observal
docker compose -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d

{_idle_cron_block(site)}
echo "=== Deploy complete ==="
"""


async def _wait_for_healthy(site: Site, timeout_seconds: int = 600) -> bool:
    import asyncio

    if get_settings().is_local:
        logger.info("[mock] Skipping health check for %s (local mode)", site.domain)
        await asyncio.sleep(2)
        return True

    urls = [f"https://{site.domain}/readyz"]
    if site.ip_address:
        urls.append(f"http://{site.ip_address}/readyz")

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    async with httpx.AsyncClient(verify=False) as client:
        while asyncio.get_running_loop().time() < deadline:
            for url in urls:
                try:
                    resp = await client.get(url, timeout=5)
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
) -> Site:
    default_infra, default_remote, default_github = _get_defaults()
    infra = infra or default_infra
    remote = remote or default_remote
    github = github or default_github

    try:
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

        # Stage 3: Deploy application
        transition_status(site, SiteStatus.DEPLOYING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="deploying", message="Deploying application...")
        script = _deploy_script(site, sha)
        cmd_result: CommandResult = await remote.run_command(result.instance_id, script)
        if cmd_result.status != "success":
            raise RuntimeError(f"Deploy script failed: {cmd_result.output[:500]}")

        # Stage 4: Wait for healthy
        await publish_site_event(str(site.id), "stage_progress", message="Waiting for health check...")
        healthy = await _wait_for_healthy(site)
        if not healthy:
            raise RuntimeError(f"Site {site.domain} did not become healthy within timeout")

        # Stage 5: Success
        transition_status(site, SiteStatus.RUNNING)
        site.last_deployed_at = datetime.now(timezone.utc)
        site.error_message = None
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="running", message="Site is live")

        # Stage 6: Notify
        await send_site_notification(site, "ready")
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
        await send_site_notification(site, "failed")
        raise

    return site


async def destroy_site(
    db: AsyncSession,
    site: Site,
    *,
    infra: TerraformRunner | None = None,
    remote: SSMRunner | None = None,
) -> Site:
    default_infra, default_remote, _ = _get_defaults()
    infra = infra or default_infra
    remote = remote or default_remote

    try:
        transition_status(site, SiteStatus.DESTROYING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="destroying", message="Destroying site...")

        # Stage 1: Stop application (best-effort — instance may be unreachable)
        if site.instance_id:
            try:
                await remote.run_command(site.instance_id, "cd /opt/observal && docker compose down", timeout_seconds=60)
            except Exception:
                logger.warning("Could not stop application on %s (instance may be unreachable)", site.instance_id)

        # Stage 2: Destroy infrastructure (always runs)
        await infra.destroy(site_name=site.name)

        # Stage 3: Mark destroyed
        site.status = SiteStatus.DESTROYED
        site.destroyed_at = datetime.now(timezone.utc)
        site.instance_id = None
        site.ip_address = None
        await db.commit()

        db.add(AuditLog(user_id=site.created_by, site_id=site.id, action="site.destroyed", details=audit_details(site)))
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="destroyed", message="Site destroyed")

        await send_site_notification(site, "destroyed")
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
) -> Site:
    default_infra, default_remote, default_github = _get_defaults()
    remote = remote or default_remote
    github = github or default_github

    try:
        # Wake if sleeping
        if site.status == SiteStatus.SLEEPING:
            await publish_site_event(str(site.id), "stage_progress", message="Waking from sleep...")
            await remote.run_command(site.instance_id, "cd /opt/observal && docker compose start")
            site.status = SiteStatus.RUNNING
            await db.commit()

        # Resolve new SHA
        sha = await github.resolve_ref(site.deploy_type.value, site.deploy_ref)
        site.resolved_sha = sha

        transition_status(site, SiteStatus.DEPLOYING)
        await db.commit()
        await publish_site_event(str(site.id), "status_change", status="deploying", message="Redeploying...")

        # Deploy updated code
        update_script = f"""#!/bin/bash
set -euo pipefail
exec > /var/log/flare-deploy.log 2>&1
cd /opt/observal
git fetch origin {shlex.quote(sha)}
git checkout {shlex.quote(sha)}
docker compose -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d
"""
        cmd_result = await remote.run_command(site.instance_id, update_script)
        if cmd_result.status != "success":
            raise RuntimeError(f"Redeploy script failed: {cmd_result.output[:500]}")

        # Check health
        healthy = await _wait_for_healthy(site)

        if healthy:
            transition_status(site, SiteStatus.RUNNING)
            site.last_deployed_at = datetime.now(timezone.utc)
            site.error_message = None
            db.add(AuditLog(user_id=site.created_by, site_id=site.id, action="site.redeployed", details=audit_details(site, resolved_sha=sha)))
            await db.commit()
            await publish_site_event(str(site.id), "status_change", status="running", message="Redeploy complete")
            await send_site_notification(site, "ready")
        elif site.auto_wipe_on_failure:
            # Wipe volumes and retry
            logger.warning("Site %s unhealthy after redeploy, wiping volumes", site.name)
            await publish_site_event(str(site.id), "stage_progress", message="Health check failed, wiping data and retrying...")
            wipe_script = """#!/bin/bash
set -euo pipefail
cd /opt/observal
docker compose -f docker/docker-compose.yml -f docker/docker-compose.production.yml down -v
docker compose -f docker/docker-compose.yml -f docker/docker-compose.production.yml up -d
"""
            await remote.run_command(site.instance_id, wipe_script)
            healthy = await _wait_for_healthy(site)
            if healthy:
                transition_status(site, SiteStatus.RUNNING)
                site.last_deployed_at = datetime.now(timezone.utc)
                site.error_message = None
                await db.commit()
                await publish_site_event(str(site.id), "status_change", status="running", message="Redeploy complete (data wiped)")
                await send_site_notification(site, "ready_after_wipe")
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
        await send_site_notification(site, "failed")
        raise

    return site
