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
target_quotas = {target_quotas!r}
target_assigned = [0] * len(target_refs)
active = {{}}
results = []
stop_reason = None
max_seen = 0
last_reported = -10
base = time.monotonic()
started = dt.datetime.now(dt.timezone.utc)


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


def report_progress(force=False):
    global last_reported
    completed = len(results)
    if not force and completed - last_reported < 10:
        return
    target_progress = []
    for target_ref in target_refs:
        finished = [item for item in results if item["target_ref"] == target_ref]
        target_progress.append({{
            "target_ref": target_ref,
            "launched": sum(item["target_ref"] == target_ref for item in results)
                + sum(item["target_ref"] == target_ref for item in active.values()),
            "successful": sum(
                item["return_code"] == 0 and item["verified"] for item in finished
            ),
            "failed": sum(
                item["return_code"] != 0 or not item["verified"] for item in finished
            ),
            "active": sum(item["target_ref"] == target_ref for item in active.values()),
        }})
    payload = json.dumps({{
        "instance_index": instance_index,
        "launched": launched,
        "successful": sum(item["return_code"] == 0 and item["verified"] for item in results),
        "failed": sum(item["return_code"] != 0 or not item["verified"] for item in results),
        "active": len(active),
        "max_concurrency": max_seen,
        "elapsed_seconds": round(time.monotonic() - base, 3),
        "targets": target_progress,
    }}).encode()
    last_reported = completed
    if force:
        send_progress(payload)
    else:
        threading.Thread(target=send_progress, args=(payload,), daemon=True).start()


def reap():
    global stop_reason
    for process, item in list(active.items()):
        return_code = process.poll()
        if return_code is None:
            continue
        item["log_handle"].close()
        archive = item["archive"]
        verified = False
        if return_code == 0 and archive.exists():
            try:
                with tarfile.open(archive) as tar:
                    verified = bool(tar.getmembers())
            except tarfile.TarError:
                verified = False
        error = ""
        if not verified:
            log_path = item["directory"] / "pull.log"
            error = log_path.read_text(errors="replace")[-500:] if log_path.exists() else "missing archive"
            if stop_reason is None:
                stop_reason = f"trial {{item['trial']}} failed: {{error}}"
        results.append({{
            "target_ref": item["target_ref"],
            "return_code": return_code,
            "verified": verified,
        }})
        shutil.rmtree(item["directory"])
        del active[process]
    report_progress()


def next_target_index(step):
    candidates = [
        index
        for index, quota in enumerate(target_quotas)
        if target_assigned[index] < quota
    ]
    if not candidates:
        raise RuntimeError("weighted schedule exhausted before requested pull count")
    # Largest proportional deficit gives a smooth weighted round-robin order,
    # while the precomputed quotas preserve the exact requested total.
    chosen = max(
        candidates,
        key=lambda index: ((step + 1) * target_quotas[index] - target_assigned[index] * requested, -index),
    )
    target_assigned[chosen] += 1
    return chosen


launched = 0
last_launch_at = None
for trial in range(1, requested + 1):
    target_time = base + (trial - 1) * interval
    if last_launch_at is not None:
        target_time = max(target_time, last_launch_at + interval)
    while True:
        reap()
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
    while len(active) >= max_concurrency:
        reap()
        if cancel_file.exists() and stop_reason is None:
            stop_reason = "cancelled by administrator"
        if stop_reason:
            break
        if len(active) >= max_concurrency:
            time.sleep(0.05)
    if stop_reason:
        break
    target_ref = target_refs[next_target_index(launched)]

    directory = root / f"trial-{{trial:05d}}"
    config = directory / "docker-config"
    config.mkdir(parents=True)
    archive = directory / "image.tar"
    log_handle = (directory / "pull.log").open("wb")
    env = os.environ.copy()
    env["DOCKER_CONFIG"] = str(config)
    process = subprocess.Popen(
        [crane, "pull", target_ref, str(archive)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    active[process] = {{
        "trial": trial,
        "target_ref": target_ref,
        "archive": archive,
        "directory": directory,
        "log_handle": log_handle,
    }}
    launched += 1
    last_launch_at = time.monotonic()
    max_seen = max(max_seen, len(active))

while active:
    reap()
    if active:
        time.sleep(0.05)

successful = sum(item["return_code"] == 0 and item["verified"] for item in results)
failed = len(results) - successful
report_progress(force=True)
target_summaries = []
for target_ref in target_refs:
    target_results = [item for item in results if item["target_ref"] == target_ref]
    target_summaries.append({{
        "target_ref": target_ref,
        "launched": len(target_results),
        "successful": sum(item["return_code"] == 0 and item["verified"] for item in target_results),
        "failed": sum(item["return_code"] != 0 or not item["verified"] for item in target_results),
    }})
summary = {{
    "requested": requested,
    "launched": launched,
    "successful": successful,
    "failed": failed,
    "max_concurrency": max_seen,
    "interval_seconds": interval,
    "started_at": started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    "completed_at": stamp(),
    "stop_reason": stop_reason,
    "targets": target_summaries,
}}
print("{RESULT_MARKER}" + json.dumps(summary, separators=(",", ":")))
if stop_reason or launched != requested or successful != requested:
    raise SystemExit(1)
PY
"""
