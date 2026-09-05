# ADR 0004: Assistant bridge — transcript in, structured action out

Date: 2026-09-05
Status: accepted
Related: ADR 0001 (stack), ADR 0002 (job pipeline), ADR 0003 (provider registry)

## Context

M1.5 adds a live-voice path: talk into a mic (browser, rooted Echo Dot, Pi
satellite) → ASR → **an AI assistant decides the next action**. The user's
requirement was explicit: *"text goes to an AI assistant API for next
actions"*, with the concrete LLM/API **decided later**. So the assistant stage
must exist now, be swappable later, and not block the voice loop in the
meantime.

The batch pipeline (ADR 0002) already solved the same shape of problem for
summary/quotes via the `EnrichProvider` interface. The assistant is a different
concern — interactive, latency-sensitive, action-oriented — so it gets its own
interface rather than overloading enrich.

## Decision

### 1. `AssistantProvider` interface with a structured result

```python
class AssistantResponse(BaseModel):
    reply: str                    # text to speak/display back to the user
    action: str                   # open vocabulary: "speak", "navigate", "none", …
    data: dict[str, Any] = {}     # action payload ({"echo": ...}, {"url": ...})
    provider: str = "unknown"

class AssistantProvider(ABC):
    name: str
    def act(self, transcript: str, language: str = "auto") -> AssistantResponse: ...
```

`act()` is deliberately **synchronous and single-turn**: the voice service runs
it in a threadpool. Structured `action` + `data` means clients can *do*
something (open a URL, speak text, trigger a macro) instead of parsing prose —
and unknown actions degrade to display-only, per the protocol spec.

### 2. `MockAssistantProvider` is the default

Deterministic rules (time/date, `repeat after me: X` → echo, `open X` →
navigate, otherwise acknowledgement). It makes the whole voice loop testable
and demoable offline — the same property the mock ASR gives the batch pipeline.

### 3. Registry follows ADR 0003 exactly

`ECHONEURA_ASSISTANT_PROVIDER` ∈ `{mock, openai, anthropic}` (default `mock`).
`openai`/`anthropic` are **reserved names that raise `ProviderUnavailable`
until implemented** — loud, not silently-mocked, so a future contributor knows
exactly where to plug in. Adding a real provider = one class + one registry
branch, same recipe as ASR.

### 4. No LLM call in the WebSocket hot path… yet

With the mock, `act()` is sub-millisecond. When a real provider lands, it must
respect the same synchronous contract (server calls it in a threadpool) or the
service must move to async — an accepted future refactor, noted here so the
interface isn't mistaken for a hard constraint on latency.

### 5. Utterances are stored, not jobs

Voice utterances get their own table (`voice_utterances`) instead of reusing
the batch `jobs` pipeline: they are interactive, seconds-long, and have no
segments/SRT/quotes stages. They still share the ASR provider stack, config,
and DB conventions, so provider swaps (mock → whisper → cloud) apply to both
paths with one env var.

## Consequences

- ✅ Voice loop works end-to-end today with zero external accounts.
- ✅ Choosing an LLM later touches only `providers/assistant.py` + registry.
- ✅ `action`/`data` give device clients (Dot, Pi) something machine-usable.
- ⚠️ The mock's "intelligence" is keyword rules — fine for plumbing
  validation, not for real use; set expectations in UI copy (done: the reply
  names the mock and the env var).
- ⚠️ Single-turn only: no conversation memory yet. History exists in the DB
  (`GET /api/voice/utterances`), so a real provider can be given recent turns
  without schema changes.
