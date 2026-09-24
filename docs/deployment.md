# Deployment (AWS)

## What Terraform creates

[infrastructure/terraform](../infrastructure/terraform) is one root module, split by concern:

| File | Resources |
|---|---|
| `network.tf` | VPC, public subnets (ALB, NAT) and private subnets (tasks, RDS, Redis) across 2 AZs, IGW, one NAT gateway, S3 gateway endpoint, security groups (ALB → API only; worker has no ingress; DB/Redis only from API and worker) |
| `alb.tf` | ALB, IP target group on `/health/live`, HTTP listener (forwards, or redirects to HTTPS when `certificate_arn` is set), HTTPS listener (TLS 1.3 policy) |
| `ecs.tf` | ECR (immutable tags, scan on push), cluster with Container Insights, task definitions `api` / `worker` / `beat` / `migrate` (same image, different command), zero-count bootstrap mode, services with circuit-breaker rollback, API CPU autoscaling |
| `data.tf` | S3 audio bucket (public access blocked, SSE, TLS-only policy, CORS for web origins, one-day `staging/` expiry, abort incomplete multipart), RDS PostgreSQL 17 (encrypted, private, backups, deletion protection), ElastiCache Redis (TLS, at-rest encryption), Secrets Manager secrets |
| `iam.tf` | Execution role (ECR, logs, read three secrets). API task role can read/write staging and finalize into `audio/*`; worker role can read/delete finalized audio and delete stale staging. Both have bucket listing only where needed for object checks. |
| `observability.tf` | Log groups (30 days), metric filters over JSON logs, alarms (target 5xx, no running workers) → SNS |

Validation performed: `terraform fmt -check -recursive` and `terraform validate` (with
`init -backend=false`). **It has not been applied**, because no AWS account or
credentials were available for this project. `terraform plan` needs credentials, since
it reads availability zones and the caller identity.

## Prerequisites

1. An AWS account and credentials with rights to create the resources above.
2. A remote state backend: S3 with encryption and a lockfile. See the commented block in
   `versions.tf`. State contains the generated DB password and Django secret.
3. Optional: a domain plus an ACM certificate (`certificate_arn`) for HTTPS. Without it
   the ALB serves plain HTTP, which is only acceptable for a throwaway demo.
4. An OpenAI API key (cloud providers are the default in AWS).

## First deploy

```bash
cd infrastructure/terraform
terraform init
terraform apply -var 'certificate_arn=arn:aws:acm:…' -var 'allowed_hosts=["api.example.com"]' \
                -var 'image_tag=v1' -var 'services_enabled=false'

# 1. Foundation is now provisioned, but API/worker/beat desired counts are zero.
# Set the provider key out-of-band so it never enters state
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw openai_api_key_secret_arn)" \
    --secret-string "sk-…"

# 2. Build and push the immutable image before any long-lived task can start
ECR=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${ECR%/*}"
docker build -t "$ECR:v1" ../../backend && docker push "$ECR:v1"

# 3. Run migrations as a one-off task while long-lived services remain disabled
aws ecs run-task --cluster "$(terraform output -raw ecs_cluster)" \
  --task-definition "$(terraform output -raw migrate_task_definition)" --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$(terraform output -json private_subnet_ids | jq -r 'join(",")')],securityGroups=[$(terraform output -raw api_security_group_id)]}"

# Wait for the migration task to exit successfully, then enable services.
terraform apply -var 'certificate_arn=arn:aws:acm:…' -var 'allowed_hosts=["api.example.com"]' \
                -var 'image_tag=v1' -var 'services_enabled=true'
```

Subsequent deploys in this reference favor explicit safe ordering over zero downtime:
push a new immutable tag, apply that tag with `services_enabled=false`, run and verify
the migration task, then apply the same tag with `services_enabled=true`. A production
pipeline that requires zero downtime should separate task-definition registration from
service rollout and use backward-compatible migrations.

Seeded prompt templates arrive through a data migration. Users need an auth token; the
demo `ensure_demo_user` command should not be used in production. A real deployment
adds a login flow.

## Sizing and cost notes

- API: 2 × 0.5 vCPU / 1 GB, scaling on 60% CPU up to 6. The API does only DB work, so it is cheap.
- Worker: 2 × 1 vCPU / 2 GB, concurrency 4. Cloud providers make it I/O bound. Scale on
  queue depth: publish `LLEN celery` from a scheduled Lambda or task as a custom metric,
  then add a target-tracking policy. This is not implemented; it is listed as a gap.
- The single NAT gateway and single-node Redis are cost choices. For HA use one NAT per
  AZ and a Redis replica with automatic failover, and set `db_multi_az=true`.
- Fargate ephemeral storage is 20 GiB by default. Audio is streamed to a temp dir and
  deleted per stage. Raise `ephemeralStorage` for files near that size.

## Operations

| Task | How |
|---|---|
| Re-publish stalled jobs | Celery beat schedules `requeue_stalled_analysis_jobs`; `python manage.py requeue_stalled_jobs` remains a manual repair tool |
| Expire abandoned uploads / retry object cleanup | Celery beat schedules `maintain_uploads`; `python manage.py purge_deleted_objects` remains a manual repair tool |
| Inspect jobs | Django admin (read-only for jobs and results) |
| New template version | admin or shell: `PromptTemplate.objects.get(slug=…, active=True).new_version(analysis_instructions=…)` |
| Dashboards | CloudWatch namespace `VoiceAnalysis/<env>`: JobsCreated, JobsCompleted, JobsFailed{ErrorCode}, StageLatencyMs{Stage,Model}, LLMOutputTokens{Model}, StageRetries{ErrorCode} |
