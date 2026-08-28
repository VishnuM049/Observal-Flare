from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.experiment_weights import ExperimentWeightError, allocate_weighted_pulls, normalize_weights
from server.models.audit_log import AuditLog
from server.models.experiment import Experiment, ExperimentEvent, ExperimentStatus
from server.models.user import User

IMAGE_REF_RE = re.compile(
    r"^ghcr\.io/(?P<namespace>[A-Za-z0-9](?:[A-Za-z0-9_.-]*))/(?P<package>[A-Za-z0-9][A-Za-z0-9_./-]*)"
    r"(?:(?:@sha256:(?P<digest>[a-f0-9]{64}))|(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})))$"
)
DOWNLOAD_COUNT_RE = re.compile(r'Total downloads[^<]*</span>\s*<h3 title="(\d+)"')
REGISTRY_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
ACTIVE_EXPERIMENT_STATUSES = {
    ExperimentStatus.PENDING,
    ExperimentStatus.PROVISIONING,
    ExperimentStatus.RUNNING,
    ExperimentStatus.DESTROYING,
}


class ExperimentError(Exception):
    pass


@dataclass(frozen=True)
class ImagePreflight:
    requested_ref: str
    target_ref: str
    package_url: str
    platform: str
    image_size_bytes: int
    layer_count: int


def create_progress_token(experiment_id: uuid.UUID, instance_index: int | None = None) -> str:
    """Create a callback token, optionally scoped to one fleet member."""
    serializer = URLSafeTimedSerializer(get_settings().secret_key, salt="ghcr-experiment-progress")
    payload: dict[str, str | int] = {"experiment_id": str(experiment_id), "purpose": "progress"}
    if instance_index is not None:
        payload["instance_index"] = instance_index
    return serializer.dumps(payload)


def validate_progress_token(
    token: str,
    experiment_id: uuid.UUID,
    instance_index: int | None = None,
) -> None:
    serializer = URLSafeTimedSerializer(get_settings().secret_key, salt="ghcr-experiment-progress")
    # Keep callbacks valid for a full run plus provisioning and cleanup headroom.
    max_age = max(26 * 60 * 60, get_settings().ghcr_experiment_max_duration_minutes * 60 + 2 * 60 * 60)
    try:
        payload = serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired) as exc:
        raise ExperimentError("Invalid or expired progress token") from exc
    expected: dict[str, str | int] = {"experiment_id": str(experiment_id), "purpose": "progress"}
    if instance_index is not None:
        expected["instance_index"] = instance_index
    if payload != expected:
        raise ExperimentError("Invalid progress token")


def validate_target_ref(target_ref: str) -> re.Match[str]:
    match = IMAGE_REF_RE.fullmatch(target_ref)
    if match is None:
        raise ExperimentError("Image must be a public ghcr.io reference using a tag or sha256 digest")
    return match


async def package_url_for_ref(target_ref: str) -> str:
    match = validate_target_ref(target_ref)
    namespace = match.group("namespace")
    package = quote(match.group("package"), safe="")
    api_url = f"https://api.github.com/users/{quote(namespace, safe='')}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        response = await client.get(api_url, headers={"Accept": "application/vnd.github+json"})
    if response.status_code != 200:
        raise ExperimentError(f"Could not resolve GitHub package owner for {namespace}")
    owner_kind = response.json().get("type")
    owner_path = "orgs" if owner_kind == "Organization" else "users"
    return f"https://github.com/{owner_path}/{namespace}/packages/container/package/{package}"


async def preflight_image(target_ref: str, platform: str = "linux/amd64") -> ImagePreflight:
    match = validate_target_ref(target_ref.strip())
    repository = f"{match.group('namespace')}/{match.group('package')}"
    requested_digest = f"sha256:{match.group('digest')}" if match.group("digest") else None
    registry_reference = requested_digest or match.group("tag")
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        token_response = await client.get(
            "https://ghcr.io/token",
            params={"service": "ghcr.io", "scope": f"repository:{repository}:pull"},
        )
        if token_response.status_code != 200:
            raise ExperimentError(f"GHCR token request returned HTTP {token_response.status_code}")
        token = token_response.json().get("token")
        if not token:
            raise ExperimentError("GHCR did not issue an anonymous pull token; the package may not be public")
        headers = {"Accept": REGISTRY_ACCEPT, "Authorization": f"Bearer {token}"}
        manifest_url = f"https://ghcr.io/v2/{repository}/manifests/{registry_reference}"
        response = await client.get(manifest_url, headers=headers)
        if response.status_code != 200:
            raise ExperimentError(f"GHCR manifest request returned HTTP {response.status_code}")
        returned_digest = response.headers.get("Docker-Content-Digest")
        if not returned_digest or not re.fullmatch(r"sha256:[a-f0-9]{64}", returned_digest):
            raise ExperimentError("GHCR did not return a valid canonical digest")
        if requested_digest and returned_digest != requested_digest:
            raise ExperimentError("GHCR returned a different digest than requested")
        manifest = response.json()

        if "manifests" in manifest:
            os_name, architecture = platform.split("/", 1)
            candidates = [
                item
                for item in manifest["manifests"]
                if item.get("platform", {}).get("os") == os_name
                and item.get("platform", {}).get("architecture") == architecture
            ]
            if len(candidates) != 1:
                raise ExperimentError(f"Image does not contain exactly one {platform} manifest")
            response = await client.get(
                f"https://ghcr.io/v2/{repository}/manifests/{candidates[0]['digest']}",
                headers=headers,
            )
            if response.status_code != 200:
                raise ExperimentError(f"GHCR platform manifest returned HTTP {response.status_code}")
            manifest = response.json()

    layers = manifest.get("layers")
    config = manifest.get("config", {})
    if not isinstance(layers, list) or not all(isinstance(item.get("size"), int) for item in layers):
        raise ExperimentError("GHCR returned an invalid image manifest")
    image_size = sum(item["size"] for item in layers) + int(config.get("size", 0))
    if image_size <= 0:
        raise ExperimentError("GHCR image manifest reported an invalid size")
    return ImagePreflight(
        requested_ref=target_ref.strip(),
        target_ref=f"ghcr.io/{repository}@{returned_digest}",
        package_url=await package_url_for_ref(target_ref),
        platform=platform,
        image_size_bytes=image_size,
        layer_count=len(layers),
    )


async def read_download_count(package_url: str) -> int:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        response = await client.get(
            package_url,
            headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 flare-ghcr-experiment"},
        )
    if response.status_code != 200:
        raise ExperimentError(f"GitHub package page returned HTTP {response.status_code}")
    matches = DOWNLOAD_COUNT_RE.findall(response.text)
    if len(matches) != 1:
        raise ExperimentError("GitHub package page download count was missing or ambiguous")
    return int(matches[0])


async def create_experiment(
    db: AsyncSession,
    *,
    user: User,
    target_refs: list[str],
    expected_resolved_refs: list[str],
    rate_per_minute: int,
    duration_minutes: int,
    concurrency_limit: int,
    instance_count: int = 1,
    target_weights: list[int] | None = None,
) -> Experiment:
    settings = get_settings()
    if not settings.ghcr_experiments_enabled:
        raise ExperimentError("GHCR experiments are disabled")
    if rate_per_minute < 1 or rate_per_minute > settings.ghcr_experiment_max_rate_per_minute:
        raise ExperimentError(
            f"Rate must be between 1 and {settings.ghcr_experiment_max_rate_per_minute} pulls per minute"
        )
    if duration_minutes < 1 or duration_minutes > settings.ghcr_experiment_max_duration_minutes:
        raise ExperimentError(
            f"Duration must be between 1 and {settings.ghcr_experiment_max_duration_minutes} minutes"
        )
    if concurrency_limit < 1 or concurrency_limit > settings.ghcr_experiment_max_concurrency:
        raise ExperimentError(
            f"Concurrency must be between 1 and {settings.ghcr_experiment_max_concurrency}"
        )
    max_instances = getattr(settings, "ghcr_experiment_max_instances", 10)
    if instance_count < 1 or instance_count > max_instances:
        raise ExperimentError(f"Instance count must be between 1 and {max_instances}")

    target_refs = [target.strip() for target in target_refs]
    try:
        weights = normalize_weights(len(target_refs), target_weights)
    except ExperimentWeightError as exc:
        raise ExperimentError(str(exc)) from exc
    if len(expected_resolved_refs) != len(target_refs):
        raise ExperimentError("Every image must be validated before starting")
    if not target_refs or len(target_refs) > settings.ghcr_experiment_max_images:
        raise ExperimentError(f"Choose between 1 and {settings.ghcr_experiment_max_images} images")
    if len(set(target_refs)) != len(target_refs):
        raise ExperimentError("Experiment images must be unique")
    if len(target_refs) > 1 and concurrency_limit > len(target_refs):
        raise ExperimentError("Concurrency cannot exceed the image count for a multi-image experiment")
    preflights = await asyncio.gather(*(preflight_image(target) for target in target_refs))
    resolved_refs = [item.target_ref for item in preflights]
    if resolved_refs != expected_resolved_refs:
        raise ExperimentError("An image tag changed after preflight; validate the images again")
    expected_pulls_per_instance = rate_per_minute * duration_minutes
    expected_pulls = expected_pulls_per_instance * instance_count
    per_instance_allocations = allocate_weighted_pulls(expected_pulls_per_instance, weights)
    targets = [
        {
            "requested_ref": item.requested_ref,
            "target_ref": item.target_ref,
            "package_url": item.package_url,
            "platform": item.platform,
            "image_size_bytes": item.image_size_bytes,
            "layer_count": item.layer_count,
            "weight": weights[index],
            "expected_pulls": per_instance_allocations[index] * instance_count,
            "estimated_transfer_bytes": (
                item.image_size_bytes * per_instance_allocations[index] * instance_count
            ),
            "launched_pulls": 0,
            "successful_pulls": 0,
            "failed_pulls": 0,
            "baseline_count": None,
            "current_count": None,
            "final_count": None,
        }
        for index, item in enumerate(preflights)
    ]
    estimated_transfer_bytes = sum(
        target["image_size_bytes"] * target["expected_pulls"] for target in targets
    )
    maximum_transfer_bytes = settings.ghcr_experiment_max_transfer_gb * 1_000_000_000
    if estimated_transfer_bytes > maximum_transfer_bytes:
        raise ExperimentError(
            "Estimated transfer exceeds the configured "
            f"{settings.ghcr_experiment_max_transfer_gb} GB experiment limit"
        )

    active = await db.execute(select(Experiment.id).where(Experiment.status.in_(ACTIVE_EXPERIMENT_STATUSES)))
    if active.scalar_one_or_none() is not None:
        raise ExperimentError("Another GHCR experiment is already active")

    experiment = Experiment(
        created_by=user.id,
        status=ExperimentStatus.PENDING,
        target_ref=preflights[0].target_ref,
        package_url=preflights[0].package_url,
        targets=targets,
        rate_per_minute=rate_per_minute,
        duration_minutes=duration_minutes,
        expected_pulls=expected_pulls,
        instance_count=instance_count,
        concurrency_limit=concurrency_limit,
        platform=preflights[0].platform,
        image_size_bytes=sum(item.image_size_bytes for item in preflights),
        layer_count=sum(item.layer_count for item in preflights),
        estimated_transfer_bytes=estimated_transfer_bytes,
        instance_type=settings.ghcr_experiment_instance_type,
        instances=[
            {
                "index": index,
                "instance_id": None,
                "status": "pending",
                "cleanup_status": "not_started",
                "launched_pulls": 0,
                "successful_pulls": 0,
                "failed_pulls": 0,
                "active_pulls": 0,
                "max_concurrency": 0,
                "last_progress_at": None,
                "last_progress_event_at": None,
                "error_message": None,
                "run_log": None,
                "targets": [
                    {
                        "target_ref": item.target_ref,
                        "weight": weights[target_index],
                        "launched": 0,
                        "successful": 0,
                        "failed": 0,
                        "active": 0,
                    }
                    for target_index, item in enumerate(preflights)
                ],
            }
            for index in range(instance_count)
        ],
        terraform_state_key="pending",
    )
    db.add(experiment)
    await db.flush()
    experiment.terraform_state_key = f"experiments/{experiment.id}/terraform.tfstate"
    db.add(
        ExperimentEvent(
            experiment_id=experiment.id,
            event_type="created",
            payload={
                "target_refs": target_refs,
                "target_weights": weights,
                "rate_per_minute": rate_per_minute,
                "duration_minutes": duration_minutes,
                "expected_pulls": expected_pulls,
                "expected_pulls_per_instance": expected_pulls_per_instance,
                "instance_count": instance_count,
                "concurrency_limit": concurrency_limit,
                "platform": "linux/amd64",
                "image_count": len(targets),
                "estimated_transfer_bytes": estimated_transfer_bytes,
            },
        )
    )
    db.add(
        AuditLog(
            user_id=user.id,
            site_id=None,
            action="experiment.created",
            details={
                "experiment_id": str(experiment.id),
                "target_refs": target_refs,
                "target_weights": weights,
                "rate_per_minute": rate_per_minute,
                "duration_minutes": duration_minutes,
                "expected_pulls": expected_pulls,
                "expected_pulls_per_instance": expected_pulls_per_instance,
                "instance_count": instance_count,
                "concurrency_limit": concurrency_limit,
                "platform": "linux/amd64",
                "image_count": len(targets),
                "estimated_transfer_bytes": estimated_transfer_bytes,
            },
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ExperimentError("Another GHCR experiment is already active") from exc
    await db.refresh(experiment)
    return experiment


async def list_experiments(db: AsyncSession) -> list[Experiment]:
    result = await db.execute(select(Experiment).order_by(Experiment.created_at.desc()))
    return list(result.scalars().all())


async def get_experiment(db: AsyncSession, experiment_id: uuid.UUID) -> Experiment:
    experiment = await db.get(Experiment, experiment_id)
    if experiment is None:
        raise ExperimentError("Experiment not found")
    return experiment
