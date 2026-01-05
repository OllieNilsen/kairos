Load and obey: ~/p/kairos/rules/kairos-implementation-rules.md

Goal: Implement PLAN-SLICE4.md (Slice 4: MVP Completion) in this AWS serverless Python repo (CDK for infra, Lambdas for runtime), while preserving all existing PLAN.md + PLAN-SLICE3.md functionality.

Critical context:
- Existing Slice 1–3 behavior is a stable contract and must not be broken.
- Slice 4 introduces: KCNF (Kairos Calendar Normal Form), Microsoft Graph integration, multi-user support, actions, briefings, and invite triage (recommend-only MVP).
- KCNF becomes the single source of truth for calendar events; legacy `kairos-meetings` table is deprecated after migration.
- Multi-user routing items (PHONE#, EMAIL#, GOOGLE#CHANNEL#, MS#SUB#) are P0 for cross-tenant safety.
- Do NOT add Lambda Powertools or X-Ray SDK dependencies in Slice 4.

Operating procedure (must follow rule file):
- Work in EXACTLY ONE PLAN-SLICE4.md phase/step at a time, then STOP for user quality gate (fmt/lint/test/build/deploy).
- 100% unit test coverage for new/changed code; deterministic tests only.
- AI-first: structured outputs, Pydantic validation; no brittle string matching.
- No model coupling: use/extend the existing `LLMClient` interface; provider code stays behind adapters.
- Idempotency for all new side effects (KCNF writes, SMS sends, schedule creation, invite decisions, briefings).

Key implementation constraints (from PLAN-SLICE4.md):
1. **KCNF Put+Update redirect pattern:** When event start_time changes, use TransactWriteItems (Put new item + Update old item to redirect tombstone). Do NOT use delete-then-put. Version guard uses `provider_version` only.
2. **Routing items:** All webhook routing must be O(1) via GetItem (PHONE#, GOOGLE#CHANNEL#, MS#SUB#). No scans/GSIs in hot paths.
3. **Google webhook verification:** Use `channel_token` (random secret set at watch creation) verified via `secrets.compare_digest()`. Google does NOT provide HMAC signatures.
4. **Microsoft Graph verification:** Use `clientState` verification with 60-minute overlap window during rotation. Early rejection before expensive operations.
5. **Briefing idempotency:** Key is `brief-sms:<user_id>#<provider>#<provider_event_id>#DAY#<YYYY-MM-DD-local>` (does NOT include start_iso, so moving a meeting does NOT create new key).
6. **Invite versioned state machine:** `decision_version` increments on staleness; execution MUST verify `user_response_version == decision_version`.
7. **Duplicate suppression:** Multi-provider events deduplicated by `canonical_event_key` (time-bucketed to minute).
8. **Item size guards:** Truncate description to 8KB, attendees to 200, to stay under DynamoDB 400KB limit.
9. **GSI_DAY computation:** Day is computed in user's local timezone from event start (not UTC).
10. **Graceful degradation:** Actions, briefings, invite triage failures must NOT break core debrief flow.

Now do ONLY this (no code yet):
1) Read PLAN.md and PLAN-SLICE3.md to understand existing architecture and contracts.
2) Read PLAN-SLICE4.md thoroughly, noting:
   - Phase ordering (4A → 4B → 4C → 4D → 4E → 4F → 4G → 4H → 4I → 4J → 4K)
   - P0 tests and invariants per phase
   - Security requirements (Section 18.2)
   - Data model (Section 5)
   - Idempotency keys (Section 8.3)
3) Scan the repo to verify what is actually implemented already (especially models, adapters, handlers from Slice 1-3).
4) Produce a "Slice 4 Step List":
   - Enumerate the phases and sub-steps from PLAN-SLICE4.md in the documented order.
   - For each step, specify: files to touch, tests to add, idempotency implications, security checks, and any risk to existing behavior.
   - Flag any steps that require changes to existing Slice 1-3 code (these need explicit approval).
5) Ask me to choose the FIRST single step to implement.

After I pick the step:
- Implement ONLY that step with TDD and deterministic unit tests.
- If you think you need to change existing behavior, STOP and ask approval before editing.
- Ensure all P0 tests from PLAN-SLICE4.md Section 17.1 relevant to this step are covered.
- When the step is complete, STOP and tell me the exact repo commands to run for format, lint, tests, build, deploy (discover from Makefile/pyproject), and wait for my confirmation before continuing.

Phase-specific guidance:

**Phase 4A (KCNF foundation):**
- Create `KairosCalendarEvent` Pydantic model with all fields from Section 4.2
- Create `kairos-calendar-events` DynamoDB table with GSI_DAY and GSI_PROVIDER_ID (fixed design from Section 5.2B)
- Implement `normalize_google_event()` → KCNF
- Implement Put+Update redirect pattern for start_time changes (TransactWriteItems)
- Implement `CalendarEventsRepository` with `get_event()`, `get_by_provider_event_id()`, `list_events_by_day()` (redirect-following, tombstone-filtering)
- Shadow-write KCNF alongside legacy meetings table (feature flag: `kcnf_enabled`)
- P0 tests: normalizer field coverage, Put+Update redirect logic, GSI_PROVIDER_ID tie-break rules

**Phase 4B (Multi-user primitives):**
- Create `kairos-users` table with routing items (PHONE#, EMAIL#)
- Create `kairos-calendar-sync-state` table with routing items (GOOGLE#CHANNEL#, MS#SUB#)
- Implement `UsersRepository` with phone/email routing lookups
- Update Twilio inbound routing: From phone → user_id via O(1) GetItem
- Update Google calendar webhook: channel_id → user_id + channel_token verification
- Ensure all idempotency keys include user_id
- P0 tests: routing logic, user_id isolation, phone enumeration protection (10/hour limit)

**Phase 4C (Microsoft Graph integration):**
- Register Azure AD app; implement `MicrosoftGraphClient` adapter
- Implement `outlook_calendar_webhook` Lambda with validationToken handshake + clientState verification
- Implement delta sync with 410 Gone fallback
- Implement `normalize_microsoft_event()` → KCNF
- P0 tests: Graph client retries, normalizer, clientState verification, early rejection path

**Phase 4D (Subscription renewal):**
- Implement `subscription_renewer` Lambda (scheduled hourly)
- Renew Google watch (rotate channel_token) and Microsoft Graph subscriptions (rotate clientState with 60-min overlap)
- Handle failures with exponential backoff (1min, 5min, 15min; max 3 retries)
- P0 tests: renewal logic, backoff, grace period, clientState rotation, overlap window acceptance/rejection

**Phase 4E (Actions + reminders):**
- Create `kairos-action-items` table + `ActionItemsRepository`
- Implement `ActionExtractor` with LLM-based extraction + evidence grounding
- Hook into post-call webhook pipeline (best-effort, graceful degradation)
- Extend SMS intents: ADD_ACTION, SET_REMINDER, LIST_ACTIONS, MARK_DONE
- Implement reminder scheduling (one-time EventBridge schedule)
- P0 tests: schema validation, evidence presence, reminder idempotency

**Phase 4F (Pre-meeting briefings):**
- Implement briefing scheduler (T - `briefings_lead_time_minutes`)
- Implement briefing generator (KG query + LLM-based, grounded in evidence)
- Implement briefing SMS sender with stable idempotency (per meeting/day, not per start_iso)
- Implement duplicate suppression via `canonical_event_key`
- Enforce rate limits (8/day) and quiet hours (22:00-07:00 local)
- P0 tests: stable idempotency under reschedule, duplicate suppression, rate limiting, quiet hours

**Phase 4G (Invite triage recommend-only):**
- Implement invite detection: query KCNF for `is_invite_candidate` events
- Implement `InviteTriage` service with LLM-based recommendation
- Create `kairos-invite-decisions` table with `decision_version` (versioned state machine)
- Implement SMS recommendation workflow + staleness detection
- Implement voided approval notification
- Rate limiting: 5/hour/user (burst: 3 in 5 min); bulk-import detection (>20/hour)
- P0 tests: versioned state machine, staleness detection, voided approval notification, rate limiting
- **DO NOT implement provider execution** (deferred to Phase 4H)

**Phase 4H (Invite execution - GATED):**
- Prerequisites: Phase 4G running 2+ weeks, versioned state machine tested
- Implement provider execution on approval (Google + Microsoft)
- Version check: execution MUST verify `user_response_version == decision_version`
- Idempotency: `invite-exec:<user_id>#<provider>#<event_id>#v<decision_version>#<action>`
- Feature flag: `invite_execution_enabled` per user (manual enablement only)

**Phases 4I-4K:** Follow PLAN-SLICE4.md Section 16 checklists.

Security reminders (enforce throughout):
- Google webhook: verify `X-Goog-Channel-Token` via `secrets.compare_digest()` BEFORE delta sync
- Microsoft webhook: verify `clientState` (current or previous within 60-min) BEFORE delta sync
- Twilio inbound: mandatory signature verification; rate limit phone lookups (10/hour)
- Never log: refresh tokens, full phone numbers, event descriptions
- All DDB queries: use USER#<user_id> partitioning (no cross-tenant access)
