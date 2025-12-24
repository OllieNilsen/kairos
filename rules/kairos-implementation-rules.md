# Kairos Implementation Rules (Generic)

These rules govern how you (the coding agent) modify this repo. Treat them as non-negotiable.

## 1) Compatibility & Change Control

- Assume existing functionality is a stable contract.
- Prefer **additive changes only**.
- If you believe you must change existing behavior (including schemas, APIs, handler flow, infra), you must **STOP BEFORE EDITING** and provide:
  - exact file(s)/symbol(s)/lines to change,
  - why it’s required,
  - risk analysis (what could break),
  - safer alternatives,
  - a minimal-diff option,
  - then ask for explicit approval.

## 2) Stepwise Execution + Quality Gates (No Racing)

- You must implement **exactly ONE plan step at a time**.
  - “One step” = one checkbox in the plan file, or one clearly-defined atomic sub-step that you name.
- After completing the step, you must **STOP** and request the user to run:
  - format
  - lint
  - unit tests
  - build/package
  - deploy
- Discover the correct commands from repo tooling (Makefile/pyproject/package scripts). If multiple exist, propose the safest default.
- Do not proceed to the next step until the user confirms the gate passed and pastes outputs (or confirms success).

## 3) Testing: 100% Unit Coverage, Deterministic

- All new modules and every modified line must be covered by unit tests.
- Unit tests must be deterministic:
  - no real AWS calls,
  - no real network calls,
  - no real model/LLM calls.
- Use fakes/stubs/fixtures. Avoid snapshot tests of long natural language.

## 4) Architecture: SOLID + Boundary Isolation

- Keep domain logic pure and testable.
- Put side effects behind interfaces (ports):
  - storage/repositories
  - clocks/timestamps
  - UUID generation
  - external APIs (AWS SDK, third parties)
  - LLM/model providers
- Keep adapters thin; handlers orchestrate; core contains logic.

## 5) AI-First, Schema-First (No Brittle Heuristics)

- Do not use brittle string matching/regex/fuzzy heuristics for semantic tasks.
- All model interactions must use structured output (JSON) validated by typed schemas (e.g., Pydantic).
- Validate and handle model output errors explicitly (retry/fallback/mark invalid as appropriate).
- Tests must assert schemas, invariants, and state transitions—not exact prose.

## 6) No Model Coupling

- Core logic must depend on an internal model interface (e.g., `LLMClient`).
- Provider-specific SDK code must be isolated to a single adapter module.
- Swapping providers/models must be configuration-level, not a refactor.

## 7) Idempotency & At-Least-Once Safety (Serverless Reality)

- Treat every handler invocation as retryable and duplicable.
- Any side effect must be idempotent:
  - conditional writes / fences,
  - deterministic keys,
  - deduplication strategy documented and tested.
- Make idempotency explicit per operation, with clear key formats.

## 8) Security & Privacy Defaults

- Never log secrets.
- Avoid logging raw transcripts/PII unless explicitly required; if needed, redact.
- Least-privilege IAM for new infra. No broad wildcards unless justified and approved.

## 9) Observability (Lightweight by Default)

- Prefer structured logs with stable correlation fields (request_id, user_id, meeting_id, call_id).
- Do not introduce new heavy observability dependencies unless requested.

## 10) Deliverables Per Step

For each completed step, provide:
- files added/changed,
- what behavior is implemented,
- what tests prove it,
- what existing behavior remains unchanged and why,
- the exact quality-gate commands to run now.

Stop after that.
