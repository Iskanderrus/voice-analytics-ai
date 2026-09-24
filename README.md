# Voice Analytics AI

Voice Analytics AI accepts an audio recording, transcribes it, runs a general structured analysis, and can run a second user-configurable analysis when its filter matches the first result.

The same application code supports cloud providers and a fully local mode with faster-whisper and Ollama.

## Architecture

```mermaid
flowchart LR
    App[Expo client] -->|create upload| API[Django / DRF]
    API -->|presigned POST| App
    App -->|audio bytes| S3[S3 / MinIO]
    App -->|create analysis| API
    API --> PG[(PostgreSQL)]
    API --> Redis[(Redis)]
    Redis --> Worker[Celery worker]
    Worker --> S3
    Worker --> STT[STT provider]
    Worker --> LLM[LLM provider]
    Worker --> PG
    App -->|poll status / result| API
```

The processing flow is explicitly orchestrated rather than agent-driven:

```text
upload
  -> transcription
  -> standard structured analysis
  -> deterministic template selection
  -> optional user/template analysis
  -> persisted result
```

State transitions, filter evaluation, schema validation, retry policy, and persistence are deterministic. LLM inference is not assumed to be deterministic, even with temperature set to zero.

The API and worker use the same backend image with different entry points. PostgreSQL owns durable job state; Redis only transports Celery messages.

## Why this shape

| Decision | Reason |
|---|---|
| Direct-to-S3 upload | Audio does not consume API memory or bandwidth. A presigned POST constrains key, content type, and maximum size at the storage boundary. |
| PostgreSQL as job state | Celery is at-least-once delivery. Durable state and uniqueness constraints make redelivery safe without pretending the queue provides exactly-once execution. |
| Short Celery stages | Transcription and LLM calls retry independently; a failed LLM request does not repeat transcription. |
| Provider interfaces at STT/LLM boundaries | Cloud and local execution share the same pipeline without abstracting the rest of the Django application. |
| Pydantic output validation | Model output is untrusted until it satisfies the expected schema. Invalid output gets a bounded repair attempt. |
| Polling from the mobile client | Processing takes seconds or minutes; polling is simpler than maintaining a push channel and survives reconnects naturally. |
| ECS Fargate instead of Kubernetes | The runtime has one API service and one worker service. ECS provides the deployment and scaling primitives without a cluster control plane. |

More detail is in [docs/design-decisions.md](docs/design-decisions.md), [docs/architecture.md](docs/architecture.md), [docs/ai-pipeline.md](docs/ai-pipeline.md), and [docs/failure-model.md](docs/failure-model.md).

## Quick start

Requirements: Docker with Compose v2, GNU make, and Python 3 for the demo client.

```bash
cp .env.example .env
make models
make offline-up
make demo
```

The first model download needs network access. Once the models and images are present, the local AI path does not call an external AI API.

The demo uses synthetic speech and exercises the same upload/status/result API as the mobile client.

### Mobile client

```bash
cd mobile
npm ci
EXPO_PUBLIC_API_URL=http://localhost:8000 \
EXPO_PUBLIC_API_TOKEN=$(make -s -C .. demo-user) \
npx expo start
```

For a physical device, expose the API and MinIO endpoints on an address reachable from the phone. The client contains no backend business rules: it selects a file, uploads it through the presigned form, creates an analysis, polls status, and renders the result.

## API

All application endpoints use token authentication.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/uploads` | Create an upload slot and receive a presigned POST |
| POST | `/api/v1/uploads/{id}/complete` | Verify the stored object and mark the upload ready |
| GET | `/api/v1/uploads/{id}` | Read upload metadata |
| DELETE | `/api/v1/uploads/{id}` | Remove the upload and derived customer data |
| POST | `/api/v1/analyses` | Create an asynchronous analysis |
| GET | `/api/v1/analyses/{id}` | Read processing status |
| GET | `/api/v1/analyses/{id}/result` | Read the completed result |
| DELETE | `/api/v1/analyses/{id}` | Delete analysis data while keeping the audio |
| GET/POST | `/api/v1/prompt-templates` | List available templates or create a user template |
| GET/DELETE | `/api/v1/prompt-templates/{id}` | Read or deactivate a user template |
| GET/POST | `/api/v1/prompt-templates/{id}/versions` | Read version history or create a new immutable version |

Health endpoints are available at `/health/live` and `/health/ready`.

## User-configurable second analysis

Every job first produces the application-defined standard result: summary, topics, sentiment, speaker count, action items, and key points.

The second analysis is controlled by a `PromptTemplate`. Built-in Sales and Support templates are included, and authenticated users can create their own templates through the API.

Example:

```json
{
  "name": "Renewal review",
  "slug": "renewal-review",
  "priority": 100,
  "analysis_type": "renewal_review",
  "analysis_instructions": "Extract renewal risks, commercial blockers and agreed next actions.",
  "filter_config": {
    "topics_contains_any": ["renewal", "contract"]
  },
  "output_schema": {
    "fields": {
      "renewal_risks": "Risks that may prevent renewal",
      "commercial_blockers": "Pricing or contractual blockers",
      "next_actions": "Explicitly agreed next actions"
    }
  }
}
```

Filters use a closed declarative vocabulary: language, allowed languages, sentiment, topic matches, minimum speaker count, and presence of action items. There is no expression language and no `eval`.

User-owned templates are evaluated before built-ins. Matching within a scope is ordered by priority and slug, so the same standard result and template set produce the same selection.

Template content is immutable once created. Changes create a new version; every result records the exact template version, provider, model, latency, and usage metadata.

User-authored analysis instructions never become system instructions. Fixed application rules and the output contract stay in the system role; the user's request is a separate user-role message, and the transcript is separately marked as untrusted data.

## Reliability

Celery may deliver a task more than once. Each processing stage therefore:

- claims the job under a short row lock;
- releases the transaction before storage/provider I/O;
- persists the result under a second short row lock;
- relies on uniqueness constraints as the final guard against duplicate durable results.

A worker can still call an external provider twice if it dies after the provider returns but before persistence. Avoiding that completely requires provider-side idempotency or a durable request/result protocol; the application guarantees one durable transcript and one durable result per stage instead.

Retryable provider failures use bounded exponential backoff with jitter. Permanent failures move the job to `FAILED`. A stalled-job sweep can republish jobs that were committed but not successfully delivered to Redis.

## Offline mode

```bash
make models
make offline-up
make demo
```

Local providers:

- speech-to-text: faster-whisper;
- structured analysis: Ollama;
- storage: MinIO;
- database: PostgreSQL;
- broker: Redis.

The offline Compose overlay isolates the worker and Ollama from external network access after model preparation. Model names remain configuration, not domain dependencies.

## AWS deployment reference

Terraform under `infrastructure/terraform` defines a production-shaped AWS deployment using ECS Fargate, RDS PostgreSQL, ElastiCache Redis, S3, Secrets Manager, an ALB, IAM roles and CloudWatch.

The Terraform is retained as infrastructure design and is validated in CI. No live AWS environment is required to run or evaluate the project.

## Known limitations

- No speaker diarization; speaker count is inferred from transcript text.
- Long transcripts are truncated at `TRANSCRIPT_MAX_CHARS`; multi-hour recordings need chunking and hierarchical analysis.
- The OpenAI transcription adapter rejects files above the provider's single-request limit rather than chunking them.
- The current mobile UI does not manage prompt templates; template management is exposed through the authenticated API.
- Token authentication is adequate for this service skeleton but a real consumer product needs login, token rotation, account recovery, and tenant policy.
- Worker autoscaling is not yet driven by queue depth.
- The stalled-job sweep is a management command; a long-lived deployment should schedule it.
- Terraform is validated in CI but cloud resources are not provisioned by CI.

## Development

```bash
make setup
make test
make lint
make typecheck
make mobile-check
make terraform-validate
```

CI runs backend formatting, linting, type checks, migration checks, and tests against PostgreSQL. It also runs mobile lint/typecheck/tests, validates Terraform and Compose files, and builds the backend image.

The repository includes tests because concurrency, ownership, prompt boundaries, retry behaviour, and state transitions are part of the design rather than incidental implementation details.
