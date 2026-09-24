# Design decisions

This document records the constraints behind the current architecture and the alternatives that become attractive as usage changes.

## Direct upload to object storage

The API creates the upload record and a short-lived presigned POST for a unique `staging/` key. The client sends the audio directly to S3 or MinIO.

This keeps large request bodies away from Django and makes API scaling independent of audio bandwidth. The POST policy enforces the staging key, content type, and exact declared byte length. Completion verifies the object and conditionally copies the verified ETag into a separate server-owned `audio/` key.

The extra server-side copy is deliberate: completion becomes an immutable trust boundary. Reusing a still-valid client POST can change staging data, but workers never read staging and the finalized processing input does not change.

A proxy upload through Django would be simpler for very small files but wastes application bandwidth and complicates request timeouts. Multipart or resumable S3 upload becomes useful for very large or unreliable mobile uploads.

## PostgreSQL owns job state

Redis carries Celery messages but is not the source of truth. The current stage, attempts, provider provenance, errors, and final results live in PostgreSQL.

This matters because Celery delivery is at least once. A broker message can be duplicated, delayed, or lost after a database commit. Persisted state lets a redelivered task decide whether there is still work to do. Celery beat periodically republishes work that is both stale and no longer protected by a live execution lease.

The cost is more database coordination than an ephemeral task-only design. At this scale that is preferable to inventing exactly-once semantics around the broker.

## Claim, work, persist

Provider calls do not run inside database transactions. A stage briefly locks the job row to acquire an execution lease, releases the transaction, performs slow I/O, and briefly locks again before persisting. A concurrent delivery with a different claim id exits while that lease is live; persistence requires the same lease identity that performed the provider call.

Holding a transaction open across transcription or an LLM call would make lock time proportional to an external service and increase contention and failure impact. The lease prevents queue duplication itself from consuming the retry budget without extending the database transaction across external I/O.

There is an unavoidable failure window after an external provider returns and before the response is committed. A worker crash there can cause the provider call to be repeated. Database uniqueness guarantees one durable result, not exactly one external call. If duplicate provider billing becomes material, the next step is provider-side idempotency or a durable request ledger with provider request IDs.

## Explicit orchestration instead of an agent loop

The workflow is known in advance: transcribe, produce the standard schema, choose a template, optionally run a second analysis, persist.

An autonomous agent would add model-driven control flow without a requirement for it. Explicit orchestration is easier to test, retry, observe, and reason about. An agent becomes relevant only if the product later allows the model to decide among tools or dynamically plan multi-step work.

## Standard analysis before custom analysis

The first result creates a small normalized representation of every conversation. Template selection runs over validated fields instead of raw transcript text.

That makes routing deterministic and keeps the filter vocabulary small. It also gives every result a common baseline even when no custom template matches.

The tradeoff is a second LLM call for jobs that also run a custom analysis. If latency or cost dominates, alternatives include a single combined schema for known templates, a cheaper classification model for routing, or batching both analyses in one provider request.

## User prompt trust boundary

Application rules and output schemas remain system-controlled. User-authored analysis instructions are sent as user-role content and cannot define system instructions.

The transcript and the standard-analysis context are also treated as untrusted data. This does not make prompt injection impossible, but it avoids deliberately promoting user data to the highest instruction level.

For a multi-tenant product, template length limits, moderation, audit history, tenant policy, and model-specific prompt-injection testing would be added.

## Restricted filters

Filters are validated JSON with a fixed set of predicates. No Python expressions, templates, or code are evaluated.

A general expression language would be more flexible but expands both security surface and debugging complexity. The current vocabulary is enough for routing on standard metadata. If requirements outgrow it, a small versioned DSL is preferable to evaluating arbitrary expressions.

## Immutable prompt versions

A template's analysis instructions, filter, and output schema cannot be edited in place. A material change creates a new version, and results store the version that produced them.

This supports reproducibility and makes behaviour changes auditable. It does create more rows and requires an explicit version-management endpoint, which is a reasonable cost for AI behaviour that otherwise changes invisibly.

## Structured model output

Both cloud and local LLM providers return raw model output to the pipeline. Pydantic validates it before it becomes application data.

Structured-output features from providers reduce invalid responses but do not replace application validation. A bounded repair request handles occasional schema drift; persistent invalid output fails the job rather than being silently coerced.

## Celery and Redis

Celery is a pragmatic fit for Django, long-running provider calls, bounded retries, and a small number of explicit background stages.

SQS is a credible alternative on AWS and would remove Redis as a broker dependency. It becomes attractive if the product is AWS-only or if broker durability/operations outweigh the value of a shared local/cloud Celery setup. Kafka is not justified by the current throughput or event-stream requirements.

## Polling instead of WebSockets

The mobile client polls analysis status every few seconds. Network failures, 5xx responses and HTTP 429 are transient; retries back off exponentially and throttling honors `Retry-After`.

Analysis jobs are slow relative to the polling interval, and reconnect semantics are trivial because status is durable. WebSockets or Server-Sent Events become useful if the product needs sub-second progress updates or many concurrent status views, but they add connection lifecycle and infrastructure complexity.

## Django and DRF

The service is dominated by relational state, ownership, migrations, admin operations, and HTTP APIs. Django provides those pieces directly and keeps the implementation close to the data model.

FastAPI would be a reasonable option for an API-only service, especially if the team already standardized on it. It would not remove the need for the database, worker, migrations, authorization, or async job model, so there is little benefit in switching solely for this workload.

## Provider boundaries

STT and LLM adapters are the only provider abstractions. The rest of the application uses concrete domain objects and Django ORM directly.

This is intentionally narrower than a repository/service abstraction around every layer. Provider substitution is a real requirement; swapping out Django ORM is not.

## Local inference

faster-whisper and Ollama allow the complete semantic pipeline to run without an external AI API after model preparation.

The local mode is useful for privacy-sensitive deployments and development, but model quality, memory, latency, and hardware acceleration become operational concerns. A real on-premises product would need model packaging, capacity guidance, model checksums, update policy, and GPU-aware deployment.

## AWS runtime

The cloud layout uses an ALB, ECS Fargate, RDS, ElastiCache, S3, Secrets Manager, and CloudWatch.

Fargate avoids host management and fits one stateless API plus one worker type. Kubernetes would become more compelling if the product accumulated many independently scaled services, GPU workloads, custom scheduling requirements, or an existing platform team.

The current Terraform uses one NAT gateway to reduce cost. That is an availability tradeoff: production across multiple availability zones would normally remove that single-AZ egress dependency or replace parts of the path with VPC endpoints.

## Scaling beyond the current design

At roughly thousands of short recordings per hour, the first pressure is worker/provider capacity, not the HTTP API. Separate STT and LLM queues, autoscale on queue depth, and enforce provider concurrency/rate budgets.

For multi-hour audio:

1. use multipart/resumable upload;
2. split audio into overlapping chunks;
3. transcribe chunks independently with timestamps;
4. merge transcript segments;
5. run hierarchical/map-reduce analysis;
6. store segment-level provenance.

For a regulated or enterprise product, add tenant isolation, retention policies, PII redaction, audit logs, customer-managed encryption where required, regional data residency, OpenTelemetry traces, SLOs, and cost budgets per tenant.
