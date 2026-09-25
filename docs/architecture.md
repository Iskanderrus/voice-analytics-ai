# Architecture

## Runtime components

| Component | Tech | Responsibility |
|---|---|---|
| API | Django 5.2 + DRF under gunicorn | Auth, validation, upload slots, job creation, status and result reads, deletion. Never touches audio bytes or AI providers. |
| Worker | Celery 5 (same image) | Runs the three pipeline stages and storage-cleanup tasks. |
| Scheduler | Celery beat (same image) | Periodically republishes stalled jobs and expires/retries upload cleanup. |
| PostgreSQL | 17 | Canonical state: uploads, jobs, transcripts, results, templates. |
| Redis | 7 | Celery broker only. There is no result backend (`CELERY_RESULT_BACKEND = None`). |
| Object storage | S3 (AWS) / MinIO (local) | Client-writable staging plus server-owned finalized audio. Workers read only finalized objects. |
| STT | faster-whisper (in the worker process) or OpenAI | Speech-to-text behind `TranscriptionProvider`. |
| LLM | Ollama or an OpenAI-compatible API | Structured analysis behind `LLMProvider`. |
| Client | Expo / React Native / TypeScript | Upload, polling, result display. |

## Code layout

```
backend/
  config/            settings (env-driven), celery app + request-id propagation, urls
  apps/common/       error model, JSON logging + context, request-id + health middleware, API error envelope
  apps/uploads/      AudioUpload, S3 wrapper (presign / head / download / delete), upload services + API, cleanup task
  apps/prompts/      PromptTemplate (immutable versions), filter vocabulary, output spec, prompt builder, template selection
  apps/analyses/     AnalysisJob / Transcript / AnalysisResult, state machine, pipeline stages, Celery tasks,
                     providers (transcription.py, llm.py), Pydantic schemas, pricing, API
  tests/             pytest: API, pipeline, state machine, templates, provider contracts, live-model tests
mobile/
  app/               expo-router screens: index (upload), analysis/[id] (status), analysis/[id]/result
  src/               typed API client, presigned upload, polling hook, UI primitives
infrastructure/terraform/   VPC, ALB, ECS, RDS, ElastiCache, S3, Secrets Manager, IAM, CloudWatch
scripts/             demo client and sample-audio generator (fixture is generated on demand)
```

Dependency direction: `analyses` → `prompts` → `analyses.schemas`, and `analyses` →
`uploads` → `common`. The pipeline module ([pipeline.py](../backend/apps/analyses/pipeline.py))
holds the business steps. [tasks.py](../backend/apps/analyses/tasks.py) only adds Celery
concerns: retry policy, time limits and next-stage routing.

## Data model

```mermaid
erDiagram
    USER ||--o{ AUDIO_UPLOAD : owns
    AUDIO_UPLOAD ||--o{ ANALYSIS_JOB : "analysed by"
    ANALYSIS_JOB ||--o| TRANSCRIPT : "has (unique)"
    ANALYSIS_JOB ||--o{ ANALYSIS_RESULT : "has (unique per kind)"
    PROMPT_TEMPLATE ||--o{ ANALYSIS_RESULT : "produced"
    PROMPT_TEMPLATE ||--o{ ANALYSIS_JOB : "selected for"

    AUDIO_UPLOAD {
        uuid id PK
        string object_key UK "server-owned final key"
        string staging_object_key UK "client upload key"
        string content_type
        bigint declared_size
        bigint stored_size
        string checksum "S3 ETag"
        string status "PENDING_UPLOAD|FINALIZING|UPLOADED|DELETED"
        string finalization_claim_id
        datetime finalization_claimed_at
        datetime uploaded_at
        datetime deleted_at
        datetime object_deleted_at
    }
    ANALYSIS_JOB {
        uuid id PK
        string analysis_profile "default|standard_only"
        string status
        smallint stage_attempts
        string stage_claim_id
        datetime stage_claimed_at
        string error_code
        string error_message
        string error_stage
        datetime started_at
        datetime completed_at
        datetime failed_at
    }
    TRANSCRIPT {
        text text
        string detected_language
        float duration_seconds
        string provider
        string provider_model
        json provider_metadata
        int latency_ms
    }
    ANALYSIS_RESULT {
        string kind "STANDARD|TEMPLATE"
        string analysis_type
        string prompt_version
        string provider
        string model
        json structured_output
        text summary
        json usage_metadata "tokens, truncation, finish reason"
        smallint output_attempts
        int latency_ms
    }
    PROMPT_TEMPLATE {
        slug slug
        int version "unique (slug, version)"
        bool active "one active per slug"
        int priority
        json filter_config
        json output_schema
    }
```

Constraints that carry correctness:

| Constraint | Protects against |
|---|---|
| `Transcript.analysis_job` one-to-one | Duplicate transcription after redelivery or a race. |
| `AnalysisResult (analysis_job, kind)` unique | Duplicate standard or template results. |
| `AnalysisResult` check: `kind=TEMPLATE ⇔ prompt_template IS NOT NULL` | Results without provenance. |
| `AnalysisJob (audio_upload, analysis_profile)` unique where `status <> 'FAILED'` | Double-submitted "Analyse" taps paying twice. |
| `PromptTemplate (slug, version)` unique; one `active` per slug | Ambiguous template selection. |
| `AudioUpload.object_key` unique | Two uploads sharing an object. |

`current_stage` is derived from `status` (plus `error_stage` for failures) instead of
being stored, so the two can never disagree.

## Transaction boundaries

The rule is **no transaction is ever open during network I/O to S3, STT or the LLM.**

```
claim    BEGIN; SELECT … FOR UPDATE; transition/acquire execution lease; COMMIT        (~ms)
work     download finalized audio; call provider; validate output                       (s–min)
persist  BEGIN; SELECT … FOR UPDATE; same stage + claim id? persist + transition; COMMIT (~ms)
```

Row locks are short-lived. Duplicate delivery, stale-owner rejection and the
paid-call/lost-result window are documented in [failure-model.md](failure-model.md).

## Upload flow

```text
POST /uploads
  -> allocate staging/{user}/{upload}/source.ext and audio/{user}/{upload}/source.ext
  -> return presigned POST for staging only, constrained to exact declared bytes

client -> staging object

POST /uploads/{id}/complete
  -> lock row briefly and acquire FINALIZING lease
  -> HEAD staging and verify size/type
  -> renew lease immediately before the conditional server-side COPY to audio/
  -> HEAD finalized audio
  -> lock row briefly; persist only if the same finalization claim still owns the row

worker -> downloads audio/ only
```

Workers read only the finalized key; staging cleanup is handled by periodic maintenance
with an S3 lifecycle rule as a backstop. The rationale for the staging/final split is in
[design-decisions.md](design-decisions.md).

## Deletion

`DELETE /uploads/{id}` first locks and tombstones the upload while deleting its derived
analysis rows, then schedules object cleanup after commit. If finalization is still live,
the tombstone retains enough ownership state to prevent cleanup from racing the in-flight
copy; late finalizers remove their own write before returning.

Celery beat republishes cleanup for tombstones and handles abandoned staging objects.
`DELETE /analyses/{id}` removes one analysis and its derived data but keeps the audio.
Analysis creation and upload deletion share the upload-row lock, so they cannot commit
contradictory ownership state.

## Observability

- **Logs.** One JSON object per line with `request_id`, `job_id`, `upload_id`, `stage`,
  `provider`, `model`, `attempt`, `duration_ms` and `error_code`, bound through context
  variables. The API's `X-Request-ID` travels in the Celery message headers, so worker
  lines carry the request id of the HTTP call that created the job.
- **Metrics.** CloudWatch Logs metric filters turn those lines into `JobsCreated`,
  `JobsCompleted`, `JobsFailed{ErrorCode}`, `StageLatencyMs{Stage,Model}`,
  `LLMOutputTokens{Model}` and `StageRetries{ErrorCode}`, with no metrics library in the
  app. Queue depth (Redis `LLEN celery`) needs a small publisher; this is documented,
  not implemented.
- **Health.** `/health/live` returns 200 while the process serves. `/health/ready` checks
  the DB (`SELECT 1`), Redis (`PING`) and the bucket (`HEAD`) with short timeouts. AI
  providers are deliberately excluded: a provider outage should fail jobs, not take the
  API out of rotation.
- **Cost.** Every transcript and result stores provider, model, usage and latency.
  [pricing.py](../backend/apps/analyses/pricing.py) is the only place prices live.
  `estimate_analysis_cost(result)` returns a `Decimal` or `None`. Local inference is
  reported with basis `local_compute`.
