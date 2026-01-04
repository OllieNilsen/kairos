Load and obey: ~/p/kairos/rules/kairos-implementation-rules.md

Goal: Implement PLAN-SLICE3.md (Slice 3: Personal Knowledge Graph) in this AWS serverless Python repo (CDK for infra, Lambdas for runtime), while preserving all existing PLAN.md functionality.

Critical context:
- Existing Slice 1–2 behavior is a stable contract and must not be broken.
- Transcripts are not currently stored; Slice 3 introduces transcript persistence and uses stored segments for extraction/verification.
- Do NOT add Lambda Powertools or X-Ray SDK dependencies in Slice 3.

Operating procedure (must follow rule file):
- Work in EXACTLY ONE PLAN-SLICE3.md step at a time, then STOP for user quality gate (fmt/lint/test/build/deploy).
- 100% unit test coverage for new/changed code; deterministic tests only.
- AI-first: structured outputs, Pydantic validation; no brittle string matching.
- No model coupling: introduce/extend an internal LLMClient interface; provider code stays behind an adapter.
- Idempotency for all new side effects (transcripts, mentions, edges, entities).

Now do ONLY this (no code yet):
1) Read PLAN.md and familiarise yourself with the architecture and design for SLICE 1 and 2.
2) Read PLAN-SLICE3.md and identify which items are already DONE/IN PROGRESS per the plan.
3) Scan the repo to verify what is actually implemented already (especially models, meeting attendee changes, calendar_webhook changes).
4) Produce a “Slice 3 Step List”:
   - Enumerate the remaining TODO/IN PROGRESS items from PLAN-SLICE3.md in a safe order.
   - For each step, specify: files to touch, tests to add, idempotency implications, and any risk to existing behavior.
5) Ask me to choose the FIRST single step to implement.

After I pick the step:
- Implement ONLY that step with TDD and deterministic unit tests.
- If you think you need to change existing behavior, STOP and ask approval before editing.
- When the step is complete, STOP and tell me the exact repo commands to run for format, lint, tests, build, deploy (discover from Makefile/pyproject), and wait for my confirmation before continuing.
