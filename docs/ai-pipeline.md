# AI pipeline

## Stages

| Stage (Celery task) | Status while running | Input | Output | Next |
|---|---|---|---|---|
| `transcribe_audio` | `TRANSCRIBING` | S3 object → temp file | `Transcript` | `ANALYSING_STANDARD` |
| `run_standard_analysis` | `ANALYSING_STANDARD` | transcript | `AnalysisResult(STANDARD)`, `selected_template` | `ANALYSING_CUSTOM` or `COMPLETED` |
| `run_custom_analysis` | `ANALYSING_CUSTOM` | transcript + standard summary/topics/sentiment + template | `AnalysisResult(TEMPLATE)` | `COMPLETED` |

Template selection is a pure DB read. It runs inside the transaction that persists the
standard result, so the choice and the result it was based on commit together.

## Provider boundary

```python
class TranscriptionProvider(Protocol):
    name: str; model: str
    def transcribe(self, audio: AudioSource) -> TranscriptionResult: ...

class LLMProvider(Protocol):
    name: str; model: str
    def generate_structured(self, request: StructuredAnalysisRequest) -> StructuredAnalysisResponse: ...
```

| | Local | Cloud |
|---|---|---|
| STT | `LocalWhisperTranscriptionProvider`: faster-whisper, CTranslate2 int8, VAD filter, model cached per process | `OpenAITranscriptionProvider`: `/audio/transcriptions`, `verbose_json` for `whisper-1` |
| LLM | `OllamaLLMProvider`: `/api/chat` with `format=<JSON schema>` (constrained decoding) | `OpenAICompatibleLLMProvider`: Chat Completions with `response_format=json_schema`; `OPENAI_BASE_URL` for vLLM, LiteLLM and similar |

Providers only **transport** data. They return raw text plus usage and translate
transport failures into the shared error model. They never parse or trust model output;
the pipeline does that the same way for every provider. The contract tests in
[test_provider_contracts.py](../backend/tests/test_provider_contracts.py) run identical
assertions against both implementations of each protocol:

- The response has content, token usage and the provider/model identity.
- Role separation reaches the wire.
- 429 maps to retryable `LLM_RATE_LIMITED`.
- 5xx and timeouts map to retryable errors.
- 404 (unknown model) maps to a permanent configuration error.
- Undecodable audio maps to a permanent `UNSUPPORTED_AUDIO`.

Selection is a `match` on `STT_PROVIDER` / `LLM_PROVIDER` in
[providers/__init__.py](../backend/apps/analyses/providers/__init__.py). It is not a
plugin registry: two implementations don't justify one.

SDK-level retries are disabled (`max_retries=0`). The pipeline owns retries. Each
execution-lease acquisition is counted in `stage_attempts`; duplicate deliveries that
find another live lease do not consume an attempt.

## Structured output handling

```
provider text ─► strip ```json fences ─► Pydantic model_validate_json
                         │ ok ─► persist validated model_dump (never the raw text)
                         │ ValidationError
                         ▼
        attempt < LLM_MAX_OUTPUT_ATTEMPTS (2)?
          yes ─► re-ask with the invalid answer + a list of errors (locations and messages only)
          no  ─► permanent LLM_INVALID_OUTPUT → job FAILED
```

- `StandardAnalysis` ([schemas.py](../backend/apps/analyses/schemas.py)) has bounded
  string and list lengths, and `sentiment` is a closed `Literal`.
- A template's output model is built from its `output_schema`
  (`{"fields": {"objections": "description", ...}}`). Each field is a `list[str]`, plus a
  `summary`. The shape is closed and simple, and it is validated exactly like the standard one.
- The JSON schema is sent twice: in the system message (for providers without
  constrained decoding) and as the provider's structured-output parameter.
- Validation errors are reported with `include_input=False`, so transcript fragments
  never reach logs or the repair prompt.
- Raw model output is **not** stored. It can contain transcript content, and the
  validated structure is what the product uses. `usage_metadata` keeps tokens,
  `done_reason`/`finish_reason` and `transcript_truncated`.

## Prompt layering

See [README §4](../README.md#4-prompt-architecture). The standard prompt version is the
constant `STANDARD_ANALYSIS_PROMPT_VERSION`. Changing the standard instructions means
bumping it, so stored results stay attributable. Template versions are rows. Content
fields are immutable after creation, and `new_version()` copies, applies changes,
increments the version and flips `active` in one transaction.

## Filters

```json
{
  "language": "en",
  "sentiment_in": ["neutral", "negative"],
  "topics_contains_any": ["pricing", "contract"]
}
```

- Vocabulary: `language`, `language_in`, `sentiment_in`, `topics_contains_any` (case-insensitive
  substring over topic labels), `min_speakers`, `has_action_items`. Conditions are ANDed.
- `FilterConfig` is a Pydantic model with `extra="forbid"`, validated in
  `PromptTemplate.clean()` on every save. Unknown keys, unknown sentiments and empty
  lists are rejected when the template is saved, not when a job runs.
- Filters evaluate the *validated* `StandardAnalysis`, never raw model text.
- Ordering: active templates by `priority DESC, slug ASC`, and the first match wins. With
  one active version per slug, this ordering is total. `{}` is a catch-all.
- No match is a normal outcome: the job completes after the standard analysis.

Topic substring matching is deliberately loose because LLM topic labels vary
("pricing", "price negotiation"). The trade-off is that a template can match more often
than intended; seeded terms are chosen with that in mind.

## Truncation and long audio

`TRANSCRIPT_MAX_CHARS` (60 000, about 15k tokens) caps what is sent to the LLM, and
truncation is recorded per result. For multi-hour recordings the plan is chunked STT
(overlapping 10-minute windows, one task each, stitched by timestamps), then a map
step (per-chunk extraction with the same schema) and a reduce step (merge and summarise).
Those are two new stages with the same lease/work/persist pattern.

## Cost accounting

- Transcription cost: `duration_seconds × per-minute price`. The duration comes from
  the provider or, as a fallback, from probing the file with PyAV.
- LLM cost: `input_tokens × input price + output_tokens × output price`, with tokens
  summed across repair attempts.
- `GET /analyses/{id}/result` returns `cost.items[]` with `usd` and `basis`
  (`estimated`, `local_compute` or `unknown_price`). `total_usd` is only set when every
  component is priced.
