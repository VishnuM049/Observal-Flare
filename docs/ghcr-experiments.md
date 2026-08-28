# Isolated GHCR download experiments

Flare can run a bounded GHCR download-count experiment on a disposable fleet of EC2 instances. Experiments are separate from Observal sites: they use their own database table, API, UI, Terraform module, state prefix, ARQ queue, and worker.

## Enable and select the target

Experiments are disabled by default. Configure production Flare with:

```dotenv
GHCR_EXPERIMENTS_ENABLED=true
GHCR_EXPERIMENT_IMAGE=ghcr.io/OWNER/PACKAGE:TAG
```

`GHCR_EXPERIMENT_IMAGE` supplies the first default shown in the creation form. An administrator can enter up to four images in the UI for an individual run without redeploying Flare. Every value must refer to a unique public GHCR package. Tags such as `latest` are resolved during preflight and the resolved SHA-256 digest is stored with the run; explicit digest references remain supported. Creation re-resolves each tag and fails if it moved after preflight, preventing a run from silently using a different image than the UI displayed.

Optional operational bounds:

```dotenv
GHCR_EXPERIMENT_INSTANCE_TYPE=t3.small
GHCR_EXPERIMENT_MAX_RATE_PER_MINUTE=48
GHCR_EXPERIMENT_MAX_DURATION_MINUTES=1440
GHCR_EXPERIMENT_MAX_CONCURRENCY=4
GHCR_EXPERIMENT_MAX_INSTANCES=10
GHCR_EXPERIMENT_MAX_IMAGES=4
GHCR_EXPERIMENT_MAX_TRANSFER_GB=50
```

Restart the API and dedicated experiment worker after changing configuration. Each experiment selects 1–`GHCR_EXPERIMENT_MAX_INSTANCES` fleet members; rate, duration, and concurrency are per instance, while expected pulls and transfer estimates are fleet-wide. Every image has a positive integer weight. Flare uses largest-remainder allocation to preserve the exact per-instance pull total, then interleaves those quotas with weighted round-robin scheduling on every member.

## Isolation

The experiment path does not use the site provisioner or `infra/site` module. It creates no DNS record, Elastic IP, inbound security-group rule, Docker installation, or Observal deployment.

- API: `/api/experiments`
- UI: `/experiments`
- queue: `arq:experiments`
- worker: `server.worker.experiment_tasks.ExperimentWorkerSettings`
- Terraform: `infra/experiment`
- state: `experiments/<experiment-id>/terraform.tfstate`

The existing site queue remains `arq:queue` and can provision, redeploy, start, stop, or destroy sites while an experiment runs.

## Safety behavior

- Admin access is required.
- Only one experiment (one fleet) may be active globally at a time.
- The server enforces rate, duration, concurrency, and estimated-transfer limits.
- A registry preflight verifies public access, platform, layer count, and compressed size before creation.
- The browser requires an exact `RUN <pull-count>` confirmation.
- Pulls are anonymous and use a unique config/output directory per process.
- Multiple targets are scheduled with weighted round-robin independently on every fleet member. Exact rounded quotas are preserved, and a target is not started again while its previous pull is active.
- Every fleet member sends an instance-scoped signed progress callback valid for the full configured run window. Pre-fleet tokens remain accepted for member zero during rolling deployments.
- Administrators can cancel an active run; Flare retries cancellation signals and queues forced Terraform cleanup if any member cannot be reached. Cancellation remains retryable in the UI.
- There are no pull retries.
- New launches stop after the first failed pull or if the concurrency cap is reached.
- Every EC2 instance schedules a safety shutdown after the requested duration plus 60 minutes.
- Terraform creates all fleet members in one experiment state and destroy runs even when provisioning or remote commands partially fail.
- Failed destroys retain state and are marked `cleanup_failed`; they are never reported as successfully destroyed.
- A five-minute janitor runs in a reserved second worker slot, revalidates staleness under a database lock, and cannot overwrite a newly completed run.
- Terraform init/apply/destroy processes have hard timeouts; timeout or ARQ cancellation terminates and reaps the complete Terraform/provider process group.
- Progress and counter timeline events are throttled, retained with hard caps, and the UI fetches only the newest page.
- Progress callbacks and final summaries are rejected if any image exceeds or violates its weighted per-member quota.
- Fleet peak concurrency is an estimate derived from the latest per-member active samples, which may not represent the exact same instant; per-member historical peaks remain visible separately.

## Production deployment

The deployment must start both workers:

```bash
arq server.worker.tasks.WorkerSettings
arq server.worker.experiment_tasks.ExperimentWorkerSettings
```

The Flare AWS identity needs the same EC2, IAM instance-profile, SSM, S3 state, DynamoDB lock, and tagging permissions already required for AWS site provisioning. The selected default VPC must provide outbound internet access because the instance retrieves the pinned `crane` release and the public GHCR image.

The API container runs Alembic migrations through `0005` on deployment. Migration `0005` also repairs fleet target rows written by an early `0004` rollout. Keep the feature flag disabled during the first deployment, verify normal site operations, then enable experiments and restart the API and experiment worker.

## Feature rollback

This is a new, additive feature and is disabled by default. The commit immediately before this feature is:

```text
80fa95e4bdbc5cc95edcc90edc068d53748f73fa
```

If the deployment fails before the feature commit is shared broadly, restore that revision on the Flare host and recreate the previous services:

```bash
git fetch origin
git checkout 80fa95e4bdbc5cc95edcc90edc068d53748f73fa
docker compose up --build -d --remove-orphans
```

If the feature commit has already been merged and other commits follow it, prefer `git revert <feature-commit>` instead of rewriting branch history. Migration `0003` is additive: the previous application ignores the extra experiment tables, so do not downgrade the production database merely to roll back application code.
