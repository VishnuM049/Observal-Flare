# Isolated GHCR download experiments

Flare can run a bounded GHCR download-count experiment on a disposable EC2 instance. Experiments are separate from Observal sites: they use their own database table, API, UI, Terraform module, state prefix, ARQ queue, and worker.

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
GHCR_EXPERIMENT_MAX_DURATION_MINUTES=60
GHCR_EXPERIMENT_MAX_CONCURRENCY=4
GHCR_EXPERIMENT_MAX_IMAGES=4
GHCR_EXPERIMENT_MAX_TRANSFER_GB=50
```

Restart the API and dedicated experiment worker after changing configuration.

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
- Only one experiment may be active at a time.
- The server enforces rate, duration, concurrency, and estimated-transfer limits.
- A registry preflight verifies public access, platform, layer count, and compressed size before creation.
- The browser requires an exact `RUN <pull-count>` confirmation.
- Pulls are anonymous and use a unique config/output directory per process.
- Multiple targets are scheduled round-robin, and the same target is never active in two parallel slots.
- The instance sends signed, two-hour progress callbacks; the UI persists and displays the event timeline.
- Administrators can cancel an active run; Flare writes a cancellation marker, stops active pull processes, and then destroys infrastructure.
- There are no pull retries.
- New launches stop after the first failed pull or if the concurrency cap is reached.
- The EC2 instance schedules a safety shutdown after the requested duration plus 15 minutes.
- Terraform destroy runs even when the remote command fails.
- Failed destroys retain state and are marked `cleanup_failed`; they are never reported as successfully destroyed.
- A five-minute janitor retries cleanup for stale experiments.

## Production deployment

The deployment must start both workers:

```bash
arq server.worker.tasks.WorkerSettings
arq server.worker.experiment_tasks.ExperimentWorkerSettings
```

The Flare AWS identity needs the same EC2, IAM instance-profile, SSM, S3 state, DynamoDB lock, and tagging permissions already required for AWS site provisioning. The selected default VPC must provide outbound internet access because the instance retrieves the pinned `crane` release and the public GHCR image.

The API container runs Alembic migration `0003` on deployment. Keep the feature flag disabled during the first deployment, verify normal site operations, then enable experiments and restart the API and experiment worker.

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
