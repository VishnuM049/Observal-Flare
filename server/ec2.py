"""EC2 instance stop/start helpers for sleep and wake operations."""
from __future__ import annotations

import asyncio
import logging

import boto3

from server.config import get_settings

logger = logging.getLogger(__name__)


def _get_client():
    settings = get_settings()
    kwargs: dict = {"region_name": settings.aws_region}
    if settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return boto3.client("ec2", **kwargs)


def _get_ssm_client():
    settings = get_settings()
    kwargs: dict = {"region_name": settings.aws_region}
    if settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return boto3.client("ssm", **kwargs)


async def _get_instance_state(client, instance_id: str) -> str:
    loop = asyncio.get_running_loop()
    resp = await loop.run_in_executor(
        None,
        lambda: client.describe_instances(InstanceIds=[instance_id]),
    )
    return resp["Reservations"][0]["Instances"][0]["State"]["Name"]


async def _wait_for_state(client, instance_id: str, target: str, timeout_seconds: int = 300) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        state = await _get_instance_state(client, instance_id)
        if state == target:
            return
        await asyncio.sleep(5)
    raise RuntimeError(f"EC2 instance {instance_id} did not reach {target} state within {timeout_seconds}s")


async def stop_ec2_instance(instance_id: str) -> None:
    loop = asyncio.get_running_loop()
    client = _get_client()
    state = await _get_instance_state(client, instance_id)

    if state == "stopped":
        logger.info("EC2 instance %s already stopped", instance_id)
        return
    if state == "stopping":
        logger.info("EC2 instance %s already stopping, waiting...", instance_id)
        await _wait_for_state(client, instance_id, "stopped")
        return

    await loop.run_in_executor(None, lambda: client.stop_instances(InstanceIds=[instance_id]))
    logger.info("EC2 stop requested for %s, waiting for stopped state...", instance_id)
    await _wait_for_state(client, instance_id, "stopped")
    logger.info("EC2 instance %s stopped", instance_id)


async def start_ec2_instance(instance_id: str, timeout_seconds: int = 300) -> None:
    loop = asyncio.get_running_loop()
    client = _get_client()
    state = await _get_instance_state(client, instance_id)

    if state == "running":
        logger.info("EC2 instance %s already running", instance_id)
        await _wait_for_ssm(instance_id, timeout_seconds=90)
        return
    if state == "pending":
        logger.info("EC2 instance %s already pending, waiting for running...", instance_id)
        await _wait_for_state(client, instance_id, "running", timeout_seconds)
        await _wait_for_ssm(instance_id, timeout_seconds=90)
        return
    if state == "stopping":
        logger.info("EC2 instance %s is stopping, waiting for stopped before starting...", instance_id)
        await _wait_for_state(client, instance_id, "stopped")

    await loop.run_in_executor(None, lambda: client.start_instances(InstanceIds=[instance_id]))
    logger.info("EC2 start requested for %s, waiting for running state...", instance_id)
    await _wait_for_state(client, instance_id, "running", timeout_seconds)
    logger.info("EC2 instance %s is running, waiting for SSM agent...", instance_id)
    await _wait_for_ssm(instance_id, timeout_seconds=90)


async def _wait_for_ssm(instance_id: str, timeout_seconds: int = 90) -> None:
    loop = asyncio.get_running_loop()
    ssm = _get_ssm_client()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        resp = await loop.run_in_executor(
            None,
            lambda: ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            ),
        )
        instances = resp.get("InstanceInformationList", [])
        if instances and instances[0].get("PingStatus") == "Online":
            logger.info("SSM agent online for %s", instance_id)
            return
        await asyncio.sleep(5)
    raise RuntimeError(f"SSM agent on {instance_id} did not come online within {timeout_seconds}s")
