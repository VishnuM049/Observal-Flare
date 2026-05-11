from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path

import boto3
from jinja2 import Environment, FileSystemLoader

from server.config import get_settings
from server.models.site import Site

logger = logging.getLogger(__name__)

_template_dir = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_template_dir)), autoescape=True)

SUBJECTS = {
    "ready": "Your Observal site {domain} is live",
    "ready_after_wipe": "Your Observal site {domain} is live (data was reset)",
    "failed": "Provisioning failed for {domain}",
    "destroyed": "Your Observal site {domain} has been destroyed",
    "pr_closed": "PR closed — {domain} will be destroyed in 24 hours",
    "stale": "Do you still need {domain}?",
    "ttl_expiring": "{domain} will be destroyed in 12 hours",
}


async def send_site_notification(site: Site, event: str) -> None:
    settings = get_settings()
    subject = SUBJECTS.get(event, f"Flare notification: {event}").format(domain=site.domain)

    try:
        template = _jinja_env.get_template(f"site_{event}.html")
    except Exception:
        template = None

    body_html = template.render(site=site, flare_url=settings.flare_base_url) if template else f"<p>{subject}</p>"

    if settings.is_local:
        logger.info("=== EMAIL (local) ===\nTo: %s\nSubject: %s\n%s\n=====================", site.requestor_email, subject, body_html)
        return

    try:
        ses = boto3.client(
            "ses",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            functools.partial(
                ses.send_email,
                Source=settings.ses_from_address,
                Destination={"ToAddresses": [site.requestor_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Html": {"Data": body_html, "Charset": "UTF-8"}},
                },
            ),
        )
    except Exception:
        logger.exception("Failed to send email to %s", site.requestor_email)
