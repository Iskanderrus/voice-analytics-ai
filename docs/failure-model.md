# Failure model

## Error codes

| Code | Where | Retryable | Typical cause |
|---|---|---|---|
| `VALIDATION_ERROR` | API | – | Malformed request body |
| `UNSUPPORTED_AUDIO` | API (415) / pipeline | no | Content type not allowed; file cannot be decoded; exceeds cloud STT limit |
| `UPLOAD_NOT_FOUND` | API (404) / pipeline | no | Unknown or other user's upload; object missing at processing time |
| `UPLOAD_INCOMPLETE` | API (409) | – | `complete` before the object exists, another finalizer owns the live lease, or analysis starts before completion |
| `UPLOAD_OBJECT_MISMATCH` | API (422) | – | Stored size or type differs from the declaration |
| `UPLOAD_TOO_LARGE` | API (413) | – | Declared size above `UPLOAD_MAX_BYTES` |
| `ANALYSIS_NOT_FOUND` / `ANALYSIS_FAILED` | API (404 / 409) | – | Unknown job / result requested for a failed job |
| `STORAGE_ERROR` | API (503) / pipeline | yes | S3/MinIO unreachable |
| `TRANSCRIPTION_FAILED` | pipeline | yes (transport) / no (no speech) | Provider 5xx or connection error / silent audio |
| `TRANSCRIPTION_TIMEOUT` | pipeline | yes (HTTP timeout) / no (soft time limit) | Slow provider / audio too long for the stage limit |
| `LLM_RATE_LIMITED` | pipeline | yes | HTTP 429 |
| `LLM_PROVIDER_ERROR` | pipeline | yes | 5xx, timeouts, Ollama unreachable |
| `LLM_INVALID_OUTPUT` | pipeline | no (after in-stage repair) | Output fails Pydantic validation twice |
| `PROVIDER_CONFIGURATION_ERROR` | pipeline | no | Missing or invalid API key, model not pulled, unknown provider name |
| `NO_MATCHING_TEMPLATE` | pipeline | no | Job in `ANALYSING_CUSTOM` without a template (should be unreachable) |
| `STAGE_ATTEMPTS_EXHAUSTED` | pipeline | no | Message redelivered past the attempt budget, e.g. repeated OOM kills |
| `INTERNAL_PROCESSING_ERROR` | pipeline / API (500) | no | A bug: generic message stored, traceback logged |

Framework-level codes also use the envelope: DRF's `NOT_AUTHENTICATED` /
`AUTHENTICATION_FAILED` (401), `METHOD_NOT_ALLOWED` (405), `PARSE_ERROR` (400,
malformed JSON) and `THROTTLED` (429), plus Django's `BAD_REQUEST` (400, e.g. a
disallowed Host), `PERMISSION_DENIED` (403), `NOT_FOUND` (404, unknown URL) and
`INTERNAL_PROCESSING_ERROR` (500).

A failed job keeps its `stage_attempts`, so the number of attempts made is visible
without the logs.

Messages stored on the job and returned to clients are fixed strings written for users.
They never include exception text, secrets or transcript content.

## Retry policy

- The budget is `PIPELINE_MAX_STAGE_ATTEMPTS` (4) per stage and counts execution-lease acquisitions, not duplicate messages that arrive while another worker owns a live lease.
- A claim stores `stage_claim_id` and `stage_claimed_at`. Every task execution gets a fresh claim token, so even overlapping redelivery of the same Celery task id exits without provider I/O while a lease is live.
- A controlled retry releases its own lease before `task.retry()`. A worker crash leaves the lease durable; redelivery can reclaim only after lease expiry, preventing overlap with a worker that may still be running.
- `PIPELINE_STAGE_LEASE_SECONDS` must remain above the longest healthy stage runtime. The default is 30 minutes, above the current task hard limits.
- Backoff remains bounded exponential backoff with jitter. LLM schema repair remains inside one stage attempt.
- Celery beat periodically publishes stalled-job recovery, so recoverable broker gaps do not depend on an operator running a command.
- Upload finalization has a separate `UPLOAD_FINALIZATION_LEASE_SECONDS` fence. The lease
  is renewed immediately before S3 COPY; it must remain longer than a healthy COPY request
  so deletion/expiry never treats an in-flight writer as finished.

## Scenarios

| Scenario | What happens | Protection |
|---|---|---|
| Task delivered after its stage advanced | It returns the durable current status and may republish the next idempotent stage. | Status under row lock. |
| Two deliveries arrive concurrently, including redelivery with the same Celery task id | The first execution acquires the lease; the second exits without consuming an attempt or calling the provider. | Per-execution claim token + lease. |
| Worker is lost during provider I/O | Early redelivery exits while the lease is live; a later redelivery or scheduled recovery can reclaim after lease/stall expiry. | `acks_late`, lease expiry, recovery sweep. |
| Old worker returns after ownership changed | Persistence and failure handling reject its stale claim id, so it cannot overwrite or fail the new owner's work. | Stage + claim-id check. |
| Worker dies after persist, before next-stage publish | Redelivery sees advanced durable state and republishes the next stage. | PostgreSQL is canonical. |
| Broker down during `POST /analyses` | The committed `CREATED` job remains pollable and scheduled recovery republishes it later. | Commit-then-publish plus beat. |
| Broker publish fails after upload DELETE | DELETE still succeeds because DB deletion already committed; scheduled maintenance republishes object cleanup. | Safe post-commit callback plus beat. |
| Upload succeeds but `/complete` never arrives | The pending slot expires and scheduled maintenance removes staging; AWS lifecycle is a second backstop. | Upload TTL + staging lifecycle. |
| Signed POST is reused after completion | Only `staging/` can change; workers continue reading the finalized `audio/` object. | Separate writable/final namespaces. |
| Two `/complete` calls overlap | One row-locked claim enters `FINALIZING`; the other returns 409 without COPY. | Upload finalization lease. |
| DELETE wins while `/complete` is in flight | The DB tombstone commits immediately, cleanup waits for the live lease, and a finalizer that already copied removes its late write before returning 404. | Tombstone + finalization claim. |
| Upload expiry races `/complete` | Maintenance cannot expire or delete staging while the finalization claim is live; an abandoned expired claim is recovered after lease expiry. | Lease-aware maintenance. |
| Analysis creation races upload deletion | Both paths lock the upload row; whichever wins determines whether the job is created or rejected, and deleted uploads are excluded from job reads. | Shared upload-row lock. |
| Mobile polling receives 429 | Polling continues with exponential backoff and honors `Retry-After`. | Transient-throttle handling. |
## Unavoidable windows

1. **Paid call, lost result.** If the process dies after the provider responds but
   before persist commits, the call is paid again on redelivery. Mitigations: the
   attempt cap bounds cost, and stages are small so the redone work is one call.
2. **Lease/timeout configuration.** The execution lease must remain above the longest healthy task runtime. The Redis visibility timeout should remain above that task runtime as well. Misconfiguration can delay recovery or permit unnecessary reacquisition.
3. **Cross-system cleanup lag.** S3, PostgreSQL and Redis are not one transaction. Recoverable tombstones and scheduled maintenance may leave an object present for a bounded period after the DB has stopped serving it.

## Verified behaviour

Automated tests ([test_pipeline.py](../backend/tests/test_pipeline.py),
[test_upload_races.py](../backend/tests/test_upload_races.py),
[test_analyses_api.py](../backend/tests/test_analyses_api.py)) cover duplicate
delivery, concurrent duplicates, upload finalization races, transient-then-success,
retry exhaustion, permanent
failure, the redelivery crash cap, unexpected errors, invalid JSON/enum/missing fields
with repair, repair exhaustion, 429 retry, custom-stage idempotency, broker outage plus
sweep, and the full pipeline through the API.

These were also exercised against the real Docker stack (Celery, Redis, Postgres, MinIO,
Ollama):

- Undecodable "audio" upload: `FAILED/UNSUPPORTED_AUDIO` in 2s, with no retries.
- Ollama stopped mid-pipeline: 3 retries (14s, 28s, 47s), then `FAILED/LLM_PROVIDER_ERROR`.
- Four duplicate messages replayed for a completed job: all skipped. Still 1 transcript
  and 2 results.
