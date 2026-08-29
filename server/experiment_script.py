from __future__ import annotations

import json
import shlex

from server.experiment_weights import allocate_weighted_pulls, normalize_weights

CRANE_VERSION = "0.21.9"
CRANE_LINUX_X86_64_SHA256 = "5c16d8ddb971cb1d5e6ed8b1e743da8224414eeba2c2762d8f1a61b2f095699e"
RESULT_MARKER = "FLARE_EXPERIMENT_RESULT="


def build_experiment_script(
    target_refs: list[str],
    rate_per_minute: int,
    expected_pulls: int,
    concurrency_limit: int,
    progress_url: str,
    progress_token: str,
    instance_index: int = 0,
    target_weights: list[int] | None = None,
    instance_count: int = 1,
) -> str:
    interval_seconds = 60 / rate_per_minute
    weights = normalize_weights(len(target_refs), target_weights)
    target_quotas = allocate_weighted_pulls(expected_pulls, weights)
    return f"""#!/bin/bash
set -euo pipefail
WORK=/tmp/flare-ghcr-experiment
mkdir -p "$WORK"
if [ -f "$WORK/CANCELLED" ]; then
  echo "Experiment cancelled before startup"
  exit 2
fi
rm -rf "$WORK/bin" "$WORK/trials"
mkdir -p "$WORK/bin" "$WORK/trials"
trap 'rm -rf "$WORK"' EXIT

CRANE_VERSION={shlex.quote(CRANE_VERSION)}
CRANE_ARCHIVE=go-containerregistry_Linux_x86_64.tar.gz
CRANE_URL="https://github.com/google/go-containerregistry/releases/download/v${{CRANE_VERSION}}/${{CRANE_ARCHIVE}}"
curl --fail --silent --show-error --location "$CRANE_URL" --output "$WORK/$CRANE_ARCHIVE"
echo "{CRANE_LINUX_X86_64_SHA256}  $WORK/$CRANE_ARCHIVE" | sha256sum --check --status
tar -xzf "$WORK/$CRANE_ARCHIVE" -C "$WORK/bin" crane
chmod +x "$WORK/bin/crane"

TARGETS_JSON={shlex.quote(json.dumps(target_refs, separators=(',', ':')))}
TARGET_QUOTAS_JSON={shlex.quote(json.dumps(target_quotas, separators=(',', ':')))}
REQUESTED={expected_pulls}
INTERVAL_SECONDS={interval_seconds:.9f}
MAX_CONCURRENCY={concurrency_limit}
PROGRESS_URL={shlex.quote(progress_url)}
PROGRESS_TOKEN={shlex.quote(progress_token)}
CANCEL_FILE="$WORK/CANCELLED"

python3 - "$WORK/bin/crane" "$TARGETS_JSON" "$WORK/trials" \
  "$REQUESTED" "$INTERVAL_SECONDS" "$MAX_CONCURRENCY" \
  "$PROGRESS_URL" "$PROGRESS_TOKEN" "$CANCEL_FILE" <<'PY'
import datetime as dt
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

(
    crane,
    targets_json,
    root_text,
    requested_text,
    interval_text,
    max_concurrency_text,
    progress_url,
    progress_token,
    cancel_file_text,
) = sys.argv[1:]
root = pathlib.Path(root_text)
target_refs = json.loads(targets_json)
cancel_file = pathlib.Path(cancel_file_text)
requested = int(requested_text)
interval = float(interval_text)
max_concurrency = int(max_concurrency_text)
instance_index = {instance_index}
instance_count = {instance_count}
target_quotas = {target_quotas!r}
target_assigned = [0] * len(target_refs)
active = {{}}
pending_retries = []
results = []
error_groups = {{}}
stop_reason = None
max_seen = 0
last_reported = -10
attempts = 0
last_attempt_at = None
consecutive_exhausted_failures = 0
saturation_started_at = None
saturation_seconds = 0.0
saturation_events = 0
base = time.monotonic()
started = dt.datetime.now(dt.timezone.utc)
MAX_RETRIES = 3
MAX_CONSECUTIVE_EXHAUSTED_FAILURES = 3
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 30.0
fleet_slot = interval / max(1, instance_count)
fleet_offset = instance_index * fleet_slot
jitter_rng = random.Random(f"{{instance_count}}:{{instance_index}}:{{requested}}")


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def send_progress(payload):
    request = urllib.request.Request(
        progress_url,
        data=payload,
        headers={{"Authorization": f"Bearer {{progress_token}}", "Content-Type": "application/json"}},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except Exception:
        pass


def logical_active():
    return len(active) + len(pending_retries)


def report_progress(force=False):
    global last_reported
    completed = len(results)
    if not force and completed - last_reported < 10:
        return
    target_progress = []
    for index, target_ref in enumerate(target_refs):
        finished = [item for item in results if item["target_ref"] == target_ref]
        in_flight = sum(item["target_ref"] == target_ref for item in active.values()) + sum(
            item["target_ref"] == target_ref for item in pending_retries
        )
        target_progress.append({{
            "target_ref": target_ref,
            "launched": target_assigned[index],
            "successful": sum(item["verified"] for item in finished),
            "failed": sum(not item["verified"] for item in finished),
            "active": in_flight,
        }})
    payload = json.dumps({{
        "instance_index": instance_index,
        "launched": launched,
        "successful": sum(item["verified"] for item in results),
        "failed": sum(not item["verified"] for item in results),
        "active": logical_active(),
        "max_concurrency": max_seen,
        "elapsed_seconds": round(time.monotonic() - base, 3),
        "targets": target_progress,
    }}).encode()
    last_reported = completed
    if force:
        send_progress(payload)
    else:
        threading.Thread(target=send_progress, args=(payload,), daemon=True).start()


def update_saturation():
    global saturation_started_at, saturation_seconds, saturation_events
    now = time.monotonic()
    saturated = len(active) >= max_concurrency
    if saturated and saturation_started_at is None:
        saturation_started_at = now
        saturation_events += 1
    elif not saturated and saturation_started_at is not None:
        saturation_seconds += now - saturation_started_at
        saturation_started_at = None


def classify_error(error):
    text = error.lower()
    transient_patterns = (
        "connection reset", "connection refused", "connection closed", "dial tcp", "i/o timeout",
        "temporary failure", "temporarily unavailable", "timeout", "timed out", "unexpected eof",
        "tls handshake timeout", "network is unreachable", "server misbehaving",
    )
    if "protocol_error" in text or "http/2" in text:
        return "http2_protocol", True
    if "too many requests" in text or re.search(r"(?:status(?: code)?|http(?:/\\S+)?)\\D+429\\b", text):
        return "rate_limited", True
    if re.search(r"(?:status(?: code)?|http(?:/\\S+)?)\\D+5\\d\\d\\b", text):
        return "http_server_error", True
    if any(pattern in text for pattern in transient_patterns):
        return "transient_network", True
    if "unauthorized" in text or "denied" in text:
        return "authorization", False
    if "manifest" in text:
        return "manifest", False
    if "no space left" in text:
        return "disk_full", False
    if "missing archive" in text:
        return "missing_archive", False
    return "process_error", False


def record_error(category, error, exhausted):
    group = error_groups.setdefault(category, {{"attempts": 0, "exhausted": 0, "sample": error[-300:]}})
    group["attempts"] += 1
    if exhausted:
        group["exhausted"] += 1


def seconds_until_attempt_slot():
    if last_attempt_at is None:
        return 0.0
    return max(0.0, last_attempt_at + interval - time.monotonic())


def start_attempt(item):
    global attempts, last_attempt_at, max_seen
    item["attempt"] += 1
    attempts += 1
    item["archive"].unlink(missing_ok=True)
    log_path = item["directory"] / f"pull-{{item['attempt']}}.log"
    log_handle = log_path.open("wb")
    env = os.environ.copy()
    env["DOCKER_CONFIG"] = str(item["config"])
    process = subprocess.Popen(
        [crane, "pull", item["target_ref"], str(item["archive"])],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    item["log_handle"] = log_handle
    item["log_path"] = log_path
    active[process] = item
    last_attempt_at = time.monotonic()
    max_seen = max(max_seen, len(active))
    update_saturation()


def reap():
    global stop_reason, consecutive_exhausted_failures
    for process, item in list(active.items()):
        return_code = process.poll()
        if return_code is None:
            continue
        item["log_handle"].close()
        del active[process]
        update_saturation()
        verified = False
        if return_code == 0 and item["archive"].exists():
            try:
                with tarfile.open(item["archive"]) as tar:
                    verified = bool(tar.getmembers())
            except tarfile.TarError:
                verified = False
        if verified:
            results.append({{
                "trial": item["trial"],
                "target_ref": item["target_ref"],
                "return_code": return_code,
                "verified": True,
                "attempts": item["attempt"],
            }})
            consecutive_exhausted_failures = 0
            shutil.rmtree(item["directory"])
            continue

        error = item["log_path"].read_text(errors="replace")[-500:] if item["log_path"].exists() else "missing archive"
        category, transient = classify_error(error)
        can_retry = (
            transient
            and item["attempt"] <= MAX_RETRIES
            and not cancel_file.exists()
            and stop_reason is None
        )
        record_error(category, error, exhausted=not can_retry)
        if can_retry:
            backoff_cap = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** (item["attempt"] - 1)))
            item["retry_ready_at"] = time.monotonic() + jitter_rng.uniform(0.0, backoff_cap)
            pending_retries.append(item)
            continue

        results.append({{
            "trial": item["trial"],
            "target_ref": item["target_ref"],
            "return_code": return_code,
            "verified": False,
            "attempts": item["attempt"],
            "error_category": category,
        }})
        shutil.rmtree(item["directory"])
        consecutive_exhausted_failures += 1
        if consecutive_exhausted_failures >= MAX_CONSECUTIVE_EXHAUSTED_FAILURES and stop_reason is None:
            stop_reason = (
                f"{{consecutive_exhausted_failures}} consecutive pulls exhausted retries; "
                f"last error category: {{category}}"
            )
    report_progress()


def terminalize_pending_retries(category):
    while pending_retries:
        item = pending_retries.pop(0)
        results.append({{
            "trial": item["trial"],
            "target_ref": item["target_ref"],
            "return_code": 1,
            "verified": False,
            "attempts": item["attempt"],
            "error_category": category,
        }})
        shutil.rmtree(item["directory"])


def launch_ready_retries():
    if stop_reason is not None or cancel_file.exists() or len(active) >= max_concurrency:
        return
    pending_retries.sort(key=lambda item: (item["retry_ready_at"], item["trial"]))
    if not pending_retries:
        return
    ready_at = pending_retries[0]["retry_ready_at"]
    if last_attempt_at is not None:
        ready_at = max(ready_at, last_attempt_at + interval)
    if ready_at > time.monotonic():
        return
    start_attempt(pending_retries.pop(0))


def service():
    global stop_reason
    reap()
    if cancel_file.exists() and stop_reason is None:
        stop_reason = "cancelled by administrator"
        for process in active:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
    if stop_reason is not None:
        terminalize_pending_retries("cancelled" if cancel_file.exists() else "aborted")
        return
    launch_ready_retries()


def next_target_index(step):
    candidates = [
        index
        for index, quota in enumerate(target_quotas)
        if target_assigned[index] < quota
    ]
    if not candidates:
        raise RuntimeError("weighted schedule exhausted before requested pull count")
    chosen = max(
        candidates,
        key=lambda index: ((step + 1) * target_quotas[index] - target_assigned[index] * requested, -index),
    )
    target_assigned[chosen] += 1
    return chosen


launched = 0
last_launch_at = None
for trial in range(1, requested + 1):
    jitter = jitter_rng.uniform(-0.35, 0.35) * fleet_slot
    target_time = base + fleet_offset + (trial - 1) * interval + jitter
    if last_launch_at is not None:
        target_time = max(target_time, last_launch_at + interval * 0.8)
    while True:
        service()
        if cancel_file.exists() and stop_reason is None:
            stop_reason = "cancelled by administrator"
        if stop_reason:
            break
        remaining = target_time - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 0.05))
    if stop_reason:
        break
    while logical_active() >= max_concurrency:
        service()
        if cancel_file.exists() and stop_reason is None:
            stop_reason = "cancelled by administrator"
        if stop_reason:
            break
        if logical_active() >= max_concurrency:
            time.sleep(0.05)
    if stop_reason:
        break
    while True:
        service()
        if stop_reason:
            break
        remaining = seconds_until_attempt_slot()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 0.05))
    if stop_reason:
        break

    target_ref = target_refs[next_target_index(launched)]
    directory = root / f"trial-{{trial:05d}}"
    config = directory / "docker-config"
    config.mkdir(parents=True)
    item = {{
        "trial": trial,
        "target_ref": target_ref,
        "archive": directory / "image.tar",
        "directory": directory,
        "config": config,
        "attempt": 0,
    }}
    launched += 1
    start_attempt(item)
    last_launch_at = time.monotonic()

while active or pending_retries:
    service()
    if active or pending_retries:
        time.sleep(0.05)

update_saturation()
successful = sum(item["verified"] for item in results)
failed = len(results) - successful
report_progress(force=True)
target_summaries = []
for target_ref in target_refs:
    target_results = [item for item in results if item["target_ref"] == target_ref]
    target_summaries.append({{
        "target_ref": target_ref,
        "launched": len(target_results),
        "successful": sum(item["verified"] for item in target_results),
        "failed": sum(not item["verified"] for item in target_results),
    }})
grouped_errors = [
    {{"category": category, **details}}
    for category, details in sorted(error_groups.items())
]
summary = {{
    "schema_version": 2,
    "requested": requested,
    "logical_pulls": launched,
    "launched": launched,
    "successful": successful,
    "failed": failed,
    "attempts": attempts,
    "retries": attempts - launched,
    "max_concurrency": max_seen,
    "saturation_seconds": round(saturation_seconds, 3),
    "saturation_events": saturation_events,
    "interval_seconds": interval,
    "fleet_offset_seconds": round(fleet_offset, 6),
    "started_at": started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    "completed_at": stamp(),
    "stop_reason": stop_reason,
    "errors": grouped_errors,
    "targets": target_summaries,
}}
print("{RESULT_MARKER}" + json.dumps(summary, separators=(",", ":")))
if stop_reason or launched != requested or successful != requested:
    raise SystemExit(1)
PY
"""
