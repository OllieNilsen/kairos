# SLICE 4 PLAN — Kairos MVP Completion

**Date:** 2026-01-04 (UTC)  
**Status:** Enhanced Draft (incorporating critical fixes & implementation guidance)

## Ship-Readiness Verdict

**Good enough.** PLAN-SLICE4 now covers all critical ship-readiness requirements:
- ✅ Routing items (O(1) phone/email/subscription lookups)
- ✅ Stable briefing idempotency (per meeting/day, not per start_iso)
- ✅ Item-size guards (400KB limit with truncation rules)
- ✅ Graph clientState overlap window (60-min acceptance of previous state)
- ✅ Cost model with guardrails ($245/month for 10 users, voice-dominant)
- ✅ **Fixed:** DynamoDB transaction pattern uses Put new + Update old (no Delete on same key) — **canonical algorithm unified throughout document**
- ✅ **Fixed:** Google webhook verification via channel_token (provider-supported, constant-time comparison)
- ✅ **Fixed:** Invite MVP contract clarified (recommend-only, no provider writes until Phase 4H)
- ✅ **Fixed:** Schedule hash length unified (24 hex chars everywhere)
- ✅ **Fixed:** Duplicate suppression reference corrected (Section 4.5)
- ✅ **Fixed:** Event log table de-scoped to post-MVP (measurement via logs/metrics for MVP)
- ✅ **Fixed:** Unified `provider_version` field (single, always-present concurrency token) — **removes P0 ingestion correctness footgun**
- ✅ **Fixed:** `canonical_event_key` uses time-bucketed start/end (floor to minute) — **encodes ±60s fuzziness by construction**
- ✅ **Fixed:** Briefing max/day unified to 8 (removed "e.g., 5" inconsistency)
- ✅ **Fixed:** `provider_version` is the ONLY concurrency guard everywhere (removed all etag/changeKey comparison references)
- ✅ **Fixed:** GSI_PROVIDER_ID lookup tie-break rules defined (`get_by_provider_event_id()` method with event > redirect preference)
- ✅ **Fixed:** Briefing schedule timing corrected (T - lead_time, not hardcoded 15 min)

All P0 correctness/security footguns, internal contradictions, ingestion correctness issues, and single-source-of-truth conflicts have been addressed. The document is ready for implementation.

**Document Updates (Comprehensive Revision):**

**ARCHITECTURE / DATA MODEL:**
- ✅ **Recurring events support (REQUIRED):** Added `RecurrenceInfo` to KCNF with `provider_series_id`, `provider_instance_id`, `is_exception`, `original_start`; P0 tests for series/instance/exceptions
- ✅ **Multi-provider per user (FORMALIZED):** Deterministic rules for Kairos-created debrief events; primary provider selection; tag discoverability; cross-provider duplicate detection
- ✅ **Strengthened Put+Update redirect:** TransactWriteItems with unified `provider_version` guard to prevent race conditions
- ✅ **Tombstone redirect pattern:** 1-hour TTL redirect at old PK/SK during start_time changes to mitigate GSI eventual consistency
- ✅ **Timezone/day semantics (PRECISE):** GSI_DAY computed in user local time with deterministic DST handling; extensive documentation
- ✅ **Retention policy:** 180-day TTL for calendar events; 365-day for debrief events; rationale documented

**SECURITY:**
- ✅ **Microsoft Graph security model (CLARIFIED):** Do NOT depend on signatures; validationToken + clientState are core controls; compensating controls for public URLs
- ✅ **ClientState overlap window:** 60-minute acceptance of {current, previous} clientState during rotation to prevent in-flight notification failures
- ✅ **IAM isolation levels (CLARIFIED):** Global operators vs per-user Lambdas; "per-user IAM isolation is illusory for global jobs"; trust code not IAM
- ✅ **Hardened SMS routing:** Mandatory Twilio signature verification; global throttling; silent response for unknown numbers; enumeration pattern detection
- ✅ **Logging hygiene policy:** NEVER log refresh tokens, full phone numbers, event descriptions; redact/hash PII; explicit safe-to-log list

**RELIABILITY / CORRECTNESS:**
- ✅ **Briefings "belt & suspenders":** Re-check eligibility at send time (meeting exists, not moved, not quiet hours, not already sent, under rate limit)
- ✅ **Versioned invite state machine:** `decision_version` increments on staleness; version included in idempotency keys; execution verifies version match
- ✅ **Improved schedule name hashing:** 24-hex SHA256(`event_id` + `start_iso`) with collision handling; determinism P0 test
- ✅ **Recurring + invites + series exceptions:** Invite detection/execution uses instance identity correctly

**OPERATIONAL EXCELLENCE:**
- ✅ **Separate dashboards:** Global operator (subscription renewer, dispatcher) vs per-user health; correlation IDs, replay tooling
- ✅ **Comprehensive runbooks:** Added 20.5 (moved event reconciliation), 20.6 (invite approval invalidation), 20.7 (briefing reschedule churn)

**COST:**
- ✅ **Voice costs included:** Bland $200/month for 10 users (75% of total cost); revised total: **$245/month** (**$24.50/user/month**)
- ✅ **Briefing LLM mitigation strategies:** Cache attendee context, cheaper model fallback, prompt minimization (post-MVP)
- ✅ **Invite triage volume protections:** Rate-limit generation (10/hour), detect bulk-import mode, suppress during first sync unless opted in

**PRIORITISATION / SEQUENCING:**
- ✅ **Multi-user primitives FIRST (Phase 4B):** Users table, routing, sync-state are foundational; moved before Outlook integration
- ✅ **Briefings WITHOUT hard KG dependency:** MVP uses deterministic attendee emails + recent summaries; disambiguation optional/gradual
- ✅ **Actions before invite triage:** Calendar-independent, lower operational risk; invites require provider writes (higher risk)

**EVALS (SECTION 24, POST-MVP):**
- ✅ **MVP measurement:** Structured logs + CloudWatch metrics only
- ✅ **Post-MVP:** Add `kairos-event-log` DynamoDB table only if pilots require durable querying beyond logs
- ✅ **Value measurement:** Debrief (acceptance rate, time-to-start, action yield, cost/user-week); Briefings (on-time rate, usefulness); Invites (approval rate, execution rate)
- ✅ **KG accuracy evals:** Grounding pass rate, unsupported-edge rejection, ambiguity backlog, duplicate entity rate, weekly manual audit (precision >90%)
- ✅ **MVP experiment plan:** Within-user crossover (4-6 weeks) to test optimization thesis (daily habit vs high-leverage meetings); net helpfulness 0-10, annoyance guardrails, cost constraints

**P0 TESTS ADDED:**
- ✅ Recurring events (series vs instance, exceptions, time shifts, moved instances)
- ✅ Timezone/day determinism (DST boundaries, all-day events, user timezone changes)
- ✅ Multi-provider (primary provider, tag rediscovery both providers, duplicate detection)
- ✅ Versioned invite state machine (version increment, execution version check, voided approval notification)
- ✅ ClientState overlap window (current/previous acceptance, expiry rejection, unknown rejection)

## 0. Critical Implementation Issues & Fixes

### 0.1 GSI_PROVIDER_ID Key Design (MUST FIX)

**Problem:** Current design has `GSI2PK = USER#<user_id>#PROVIDER#<provider>#EVENT#<provider_event_id>` with `GSI2SK = <start_iso>`. This requires knowing start_time to query by provider event ID, which defeats the purpose.

**Fix:** Invert the design:
- **GSI2PK:** `USER#<user_id>`
- **GSI2SK:** `PROVIDER#<provider>#EVENT#<provider_event_id>`

This allows efficient lookup of a specific provider event without knowing its start time.

### 0.2 KCNF Event Updates When Start Time Changes (CANONICAL)

**Problem:** The SK `EVT#<start_iso>#<provider>#<provider_event_id>` embeds start_iso. If an event's start time changes, the primary key changes.

**CANONICAL SOLUTION (MUST):** "Put new + Update old → redirect" in one TransactWriteItems
1. Query by GSI_PROVIDER_ID to find current PK/SK (use `get_by_provider_event_id()` repository method; see Section 5.2B)
2. TransactWriteItems:
   a) Put new item at new SK (Condition: must not exist)
   b) Update old item IN PLACE to `item_type="redirect"`, `redirect_to_sk=new_sk`, `ttl=now+3600`,
      with version guard using `provider_version` only (ConditionExpression: `provider_version = :provider_version`)
3. Repository get-by-SK follows redirect (bounded); list-by-day filters redirects

**Do NOT use "delete-then-put"** (non-atomic and contradicts redirect design).

**Implementation note:** Normalizer must detect start_time changes and trigger this pattern. See Section 5.2B for the exact transaction.

### 0.3 Subscription Renewal Strategy

Both Google Calendar watch and Microsoft Graph subscriptions expire and require renewal.

**Renewal mechanism:**
- **Trigger:** Scheduled Lambda (hourly) checks all subscriptions in `kairos-calendar-sync-state`
- **Grace period:** Renew when `subscription_expiry - now < 24 hours`
- **On notification:** Also check expiry opportunistically during webhook processing

**Failure handling:**
- Max 3 retry attempts with exponential backoff (1min, 5min, 15min)
- If renewal fails: mark `error_state` in sync-state table and alert via CloudWatch
- Manual intervention required for stuck renewals

**Delta link invalidation (410 Gone):**
- Fall back to full sync: list all events for user's calendar
- Re-establish delta_link/sync_token
- Update sync-state table
- Log as warning (expected after long downtime or subscription expiry)

### 0.4 Concrete Rate Limits

**Invite triage:**
- Max **5 invite SMS per hour** per user
- Burst tolerance: 3 in 5 minutes (prevent rapid-fire during bulk import)
- Tracking: use `last_invite_triage_at` + counter in user-state

**Briefings:**
- Max **8 briefings per day** per user
- Quiet hours: **22:00 - 07:00 local time** (no briefings)
- Lead time: **10 minutes** before meeting (configurable per user, range: 5-30 min)

**SMS response parsing:**
- Rate limit lookups to prevent phone number enumeration: **10 lookups per phone per hour**

## 1. Context / Baseline (What exists already)

Kairos currently has:

- Daily planner + fixed-time SMS prompt and a single debrief call/day policy, orchestrated via EventBridge Scheduler (recurring + one-time schedules). 
- Google Calendar push sync into DynamoDB, debrief event move/delete reconciliation, Twilio inbound/outbound SMS, Bland call initiation, and a post-call webhook pipeline (summary + retries + idempotency). 
- A grounded personal knowledge graph pipeline with entities/mentions/edges, evidence + verification, progressive resolution, and deterministic attendee email hard-linking.  

Slice 4 extends the system to:

- Support Outlook (Microsoft Graph) alongside Google Calendar,
- Support multiple users (multi-tenant),
- Extract and manage "actions" (assistant to-dos) from debrief calls and SMS,
- Handle incoming invites (recommend-only in MVP),
- Add pre-meeting briefings (high value, low trust risk).

Design constraints carried forward:

- Event-driven; no polling loops; keep the Scheduler/webhook spine. 
- AI-first semantic interpretation; bounded autonomy; graceful degradation (new features must not break debrief flow).

## 1. Slice 4 Goals / Non-Goals

### 1.1 Goals (MVP completion)

**P0 — Platform capability**

1. Outlook Calendar integration (Microsoft Graph) alongside Google Calendar.
2. Calendar normalization: normalize Google + Microsoft events into a Kairos Calendar Normal Form (KCNF) so downstream logic is provider-agnostic.
3. Multi-user support (thin slice): run for N pilot users with strict tenant isolation, per-user schedules, and correct webhook/SMS routing.

**P1 — "PA usefulness"**

4. Action extraction from debrief calls and inbound SMS; persist as first-class objects with evidence.
5. Reminders / nudges for actions (scheduler-driven, SMS delivery).

**P2 — Calendar management**

6. Invite handling (recommend-only MVP): detect incoming invites, recommend actions, ask user approval via SMS, capture approval intent. Execution deferred to Phase 4H (gated rollout).
7. Calendar hygiene suggestions: conflicts, missing join link, no agenda, travel infeasibility (recommend-only).

**P3 — Compounding value**

8. Pre-meeting briefings (SMS) 5–10 minutes before meetings.
9. KG ambiguity confirmation loop (optional but recommended): resolve ambiguous mentions via quick SMS (e.g., "Sam = 1 or 2?"). 

### 1.2 Non-goals (Slice 4 MVP)

- No full UI/dashboard required (admin provisioning scripts are acceptable).
- No broad autonomous invite accept/decline (recommend-only in MVP).
- Do not turn tasks/decisions into KG entities (actions are stored separately), consistent with Slice 3. 
- No Step Functions; preserve the existing orchestration model. 
## 2. Product Requirements (User-facing behavior)

### 2.1 Primary user loops

**A) Daily debrief (existing, extended to multi-user + multi-provider)**

- Daily planner runs per user (local timezone).
- Ensures today's debrief event exists; schedules prompt trigger.
- Prompt SMS asks user if a debrief call is OK.
- User replies YES/READY -> call happens.
- Post-call webhook: store transcript, summarize, run KG pipeline, extract actions, send summary + action recap.

**B) Pre-meeting briefing (new)**

- 5–10 minutes before a meeting, send a short SMS briefing:
  - Who is this (attendees/organizer)
  - What happened last time (last relevant summary snippets)
  - Open threads (from actions / recent mentions)
  - Suggested questions
  - Join link
- Ground content in stored knowledge and prior evidence (don't invent). 

**C) Invite triage (new, MVP = recommend-only)**

- When a new invite is received:
  - Send SMS: "Invite from X at Y. Recommend: `<action>` (reasons). Reply ACCEPT / DECLINE / PROPOSE / ASK AGENDA / IGNORE."
  - Store recommendation record and user reply intent in `kairos-invite-decisions`.
- **MVP: NO provider writes** (no accept/decline execution).
- **Execution happens only in Phase 4H** (gated rollout) with explicit approval + version check.

**D) Action capture + reminders (new)**

- After debrief call and during inbound SMS:
  - Capture action items with owner + optional due time + evidence.
  - Schedule reminders; allow "DONE" quick reply.

### 2.2 MVP safety policies (hard rules)

- Never silently decline/accept invites in MVP.
- Every side effect is idempotent (SMS sends, calls, calendar writes, schedule creation).
- Failures in action extraction / invite triage / briefings must not break debrief flow (graceful degradation).  
- Tenant isolation: all storage keyed by user_id. 
## 3. Architecture (Target State)

### 3.1 High-level architecture

Slice 4 keeps the Scheduler + webhook spine and adds:

- Microsoft Graph ingestion (subscriptions + delta sync),
- KCNF storage,
- Multi-user routing,
- New pipelines: action extraction, invite triage/approval, pre-meeting briefings.

### 3.2 Component responsibilities

**Calendar ingestion layer**

- Provider webhooks (Google watch + Graph subscriptions)
- Delta sync / reconciliation
- Normalize provider events into KCNF records
- (During migration) optionally continue writing/updating legacy meetings table

**Orchestration**

- Per-user daily planner schedule (recurring or dispatcher)
- Prompt schedules + retry schedules (one-time)
- Briefing schedules (one-time per meeting)

**Conversational loop**

- Twilio inbound/outbound SMS
- Bland call initiation
- Post-call webhook:
  - transcript store
  - KG pipeline (existing)
  - action extraction (new, best-effort)
  - summary delivery (existing)

**Invite handling**

- Detect invite-like events in KCNF
- Compute recommendation + reasons + confidence
- SMS approval workflow
- Execute provider write only when approved
- Audit trail persisted for every recommendation/execution

**Briefings**

- Scheduled SMS briefings before select meetings
- Query KG and prior summaries; ground in evidence
## 4. Kairos Calendar Normal Form (KCNF)

### 4.1 Goal

Avoid forking downstream logic into "Google vs Microsoft" branches. Normalize provider formats once; everything else uses KCNF.

### 4.2 KCNF data model (minimum viable)

Python/Pydantic style (line-broken for readability):

```python
from typing import Literal
from pydantic import BaseModel
from datetime import datetime

class AttendeeInfo(BaseModel):
    name: str | None = None
    email: str | None = None
    response_status: str | None = None
    optional: bool | None = None

class OrganizerInfo(BaseModel):
    name: str | None = None
    email: str | None = None

class ConferenceInfo(BaseModel):
    join_url: str | None = None
    conference_id: str | None = None
    phone: str | None = None

class RecurrenceInfo(BaseModel):
    """Recurrence metadata for recurring events (required for MVP)."""
    provider_series_id: str | None = None      # Series master ID (Google: recurringEventId, Microsoft: seriesMasterId)
    provider_instance_id: str | None = None    # Instance ID if this is an occurrence
    is_recurring_instance: bool = False        # True if this is an instance of a series
    is_exception: bool = False                 # True if modified from series pattern
    original_start: datetime | None = None     # Original start time for exceptions (before user moved it)
    recurrence_rule: str | None = None         # RRULE (for series masters only)

class KairosCalendarEvent(BaseModel):
    # Tenant + provider identity
    user_id: str
    provider: Literal["google", "microsoft"]
    provider_calendar_id: str | None = None
    provider_event_id: str
    provider_etag: str | None = None          # Google (preserved for reference/debug only)
    provider_change_key: str | None = None    # Microsoft (preserved for reference/debug only)
    provider_version: str | None = None       # Unified version guard (always set on ingest):
                                              # - Google: provider_version = provider_etag
                                              # - Microsoft: provider_version = provider_change_key
                                              # - Fallback: last_modified_at ISO string (only if provider lacks a version token)
    
    # NOTE: provider_etag (Google) and provider_change_key (Microsoft) are preserved for reference/debug,
    # but MUST NOT be used directly for concurrency guards or staleness checks. Always use provider_version.

    # Core
    title: str | None = None
    description: str | None = None
    location: str | None = None

    start: datetime  # tz-aware (MUST be tz-aware for GSI_DAY computation)
    end: datetime    # tz-aware
    is_all_day: bool = False
    status: str | None = None  # confirmed/cancelled/tentative

    # People
    organizer: OrganizerInfo | None = None
    attendees: list[AttendeeInfo] = []

    # Conferencing
    conference: ConferenceInfo | None = None

    # Recurrence (required for MVP)
    recurrence: RecurrenceInfo | None = None

    # Kairos metadata
    is_debrief_event: bool = False
    kairos_tags: dict = {}  # normalized provider extensions / tags

    # Sync/audit
    ingested_at: datetime
    last_modified_at: datetime | None = None  # Provider last modified timestamp (for reference)
```

### 4.3 Provider tagging for Kairos-created debrief events

We already rely on tagging for robust debrief identification in Google (Slice 2). Extend to Outlook.

**Google:**

- `extendedProperties.private.kairos_type = "debrief"`
- `extendedProperties.private.kairos_user_id = "<user_id>"`
- `extendedProperties.private.kairos_date = "YYYY-MM-DD"`

**Microsoft Graph:**

Use openExtensions (preferred) or singleValueExtendedProperties:

- `kairos.type = "debrief"`
- `kairos.user_id = "<user_id>"`
- `kairos.date = "YYYY-MM-DD"`

### 4.3.1 Multi-provider per user: Kairos-created event rules (REQUIRED for MVP)

**Problem:** When user has both Google + Outlook connected, where does Kairos create debrief events?

**Deterministic rules:**

1. **Primary provider determination:**
   - Use `default_calendar_provider` from `kairos-user-state` (user-configurable via SMS: "USE GOOGLE" / "USE OUTLOOK")
   - Fallback: provider with most recent calendar activity (most recent `last_sync_at` in sync-state)
   - Fallback: alphabetical (google < microsoft)

2. **Tag discoverability (MUST):**
   - Kairos MUST write tags to primary provider only (avoid cross-provider sync complexity in MVP)
   - Ingestion from BOTH providers MUST recognize Kairos tags reliably
   - Normalizer MUST set `is_debrief_event=true` when tags match

3. **Cross-provider sync edge case:**
   - If user manually syncs calendars (Google ↔ Outlook via native sync), Kairos MAY see duplicate events
   - Detection: same `title`, `start`, `end` within 1 minute from different providers
   - Mitigation: Prioritize event from provider with Kairos tags; suppress duplicate
   - Log warning for manual investigation

4. **Provider write path:**
   - Daily planner checks primary provider for existing debrief event (by tags)
   - If not found: create in primary provider with tags
   - If found: update if needed (time change, description)

5. **Tag rediscovery test (P0):**
   - Create debrief event via Kairos → verify tags present
   - Trigger delta sync → verify `is_debrief_event=true` in KCNF
   - Test for BOTH Google and Microsoft independently

### 4.4 Derived classifications (computed)

- **is_invite_candidate:**
  - organizer email != user primary email, OR
  - attendee response indicates needsAction (provider-specific mapping)
- **is_actionable_meeting:**
  - not all-day AND (has attendees OR has join_url OR has meaningful title)
- **is_debrief_event:**
  - tagged as Kairos debrief

### 4.5 Multi-provider duplicate suppression (REQUIRED FOR BRIEFINGS/INVITES — P0)

**Problem:** When user has both Google + Outlook connected, the same meeting may exist in both providers (native calendar sync, manual mirroring, or shared corporate calendars). Without suppression, briefings/invites fire twice.

**Solution: canonical_event_key for deduplication**

**Define canonical_event_key:**
```python
def floor_to_minute(dt: datetime) -> str:
    """Floor datetime to minute precision (removes seconds/microseconds)."""
    dt = dt.astimezone(UTC)
    dt = dt.replace(second=0, microsecond=0)
    return dt.isoformat()

def canonical_event_key(event: KairosCalendarEvent) -> str:
    # Normalize for comparison
    title_norm = event.title.lower().strip() if event.title else ""
    # Time-bucket to minute (encodes ±60s fuzziness by construction)
    start_utc = floor_to_minute(event.start)
    end_utc = floor_to_minute(event.end)
    organizer_email = event.organizer.email.lower() if event.organizer and event.organizer.email else ""
    
    # Hash attendee emails (order-independent)
    attendee_emails = sorted([a.email.lower() for a in event.attendees if a.email])
    attendee_hash = hashlib.sha256("|".join(attendee_emails).encode()).hexdigest()[:16]
    
    # Combine (time-bucketing ensures ±60s events share same key)
    return hashlib.sha256(
        f"{title_norm}|{start_utc}|{end_utc}|{organizer_email}|{attendee_hash}".encode()
    ).hexdigest()
```

**Duplicate detection rules:**

If multiple KCNF events share `canonical_event_key` (time-bucketed to minute, so ±60s events naturally match):

1. **Prefer Kairos-tagged event:** If any has `is_debrief_event=true`, use that one
2. **Else prefer default_calendar_provider:** Use event from user's `default_calendar_provider` setting
3. **Else prefer most recent sync:** Use event from provider with most recent `last_sync_at` in sync-state
4. **Mark others as suppressed:** Set `duplicate_suppressed=true` in KCNF item (for debugging)

**Downstream selector behavior (MUST implement):**

- **Briefing selector:** Query KCNF by GSI_DAY → deduplicate by canonical_event_key → schedule only canonical event
- **Invite triage:** Query KCNF for invite candidates → deduplicate by canonical_event_key → recommend only once
- **Daily event selection:** Query KCNF for day → deduplicate by canonical_event_key before counting "pending meetings"
- **Note:** Selectors MUST dedupe by canonical_event_key equality (no separate fuzzy matching pass required once time-bucketed)

**Edge case: Divergent updates**

If `canonical_event_key` initially matches but later diverges (user updates title in one provider only):
- Treat as two separate events (intentional divergence)
- Prefer event from `default_calendar_provider` for authoritative context
- Log warning for manual review if briefing/invite behavior seems wrong

**Why P0:** Without this, dual-provider users get duplicate briefings/invites → immediate trust failure + cost spike.
## 5. Storage / Data Model (DynamoDB)

### 5.1 Multi-tenant keying rule

All tables partition by `USER#<user_id>` patterns (consistent with Slice 3). 

### 5.2 Tables (new + extended)

#### A) kairos-users (NEW)

**Purpose:** minimal user registry; admin provisioned.

**Keys:**
- PK: `USER#<user_id>`
- SK: `PROFILE`

**Fields:**
- `primary_email`
- `phone_number_e164`
- `timezone` (IANA)
- `preferred_prompt_time` (HH:MM)
- `status` (active/paused/stopped)
- `provider_preferences` (optional: default provider for debrief event creation)
- `created_at`, `updated_at`

##### A.1 Routing Items (REQUIRED FOR MULTI-USER SAFETY — P0)

**Critical:** To avoid scans/GSIs and to enforce uniqueness (phone numbers, subscription IDs), store additional routing items in the SAME `kairos-users` table.

**1) User profile item (primary record):**
- PK: `USER#<user_id>`
- SK: `PROFILE`

**2) Phone routing item (UNIQUE — P0 for cross-tenant safety):**
- PK: `PHONE#<e164_phone>`
- SK: `ROUTE`
- Fields: `user_id`, `status`, `created_at`
- **Write rule:** `PutItem` with `ConditionExpression: attribute_not_exists(PK) AND attribute_not_exists(SK)` (enforces uniqueness)
- **Read rule:** `GetItem(PK=PHONE#<e164>, SK=ROUTE)` → O(1) lookup

**3) Email routing item (optional but recommended for admin ops):**
- PK: `EMAIL#<normalized_email>`
- SK: `ROUTE`
- Fields: `user_id`, `created_at`
- **Write rule:** `PutItem` with `ConditionExpression: attribute_not_exists(PK) AND attribute_not_exists(SK)`

**Why:** Prevents cross-tenant misrouting (wrong user receives SMS/calls) + avoids expensive scans in webhook hot paths.

#### B) kairos-calendar-events (NEW)

**Purpose:** store normalized KCNF events.

**Keys:**
- PK: `USER#<user_id>`
- SK: `EVT#<start_iso>#<provider>#<provider_event_id>`

**Indexes:**
- **GSI_DAY:** (query all events for user on a specific day in their local timezone)
  - GSI1PK = `USER#<user_id>#DAY#YYYY-MM-DD`
  - GSI1SK = `<start_iso>#<provider>#<provider_event_id>`
  - **Day computation (CRITICAL):** YYYY-MM-DD MUST be computed in **user's local timezone** from event start
  - **Deterministic rule:** Convert `event.start` (tz-aware) to user's timezone (from `kairos-users.timezone`), format as YYYY-MM-DD
  - **DST boundary handling:** Use `pytz` or `zoneinfo`; apply user timezone directly (Python handles DST transitions automatically)
  - **Example:** Event at 2025-03-10T02:30:00Z, user in America/New_York (EST→EDT at 2AM) → day = "2025-03-09" (pre-DST)
  - **Normalizer contract:** MUST compute GSI1PK identically for same event/user regardless of normalizer invocation time
- **GSI_PROVIDER_ID:** (lookup by provider event ID without knowing start time)
  - GSI2PK = `USER#<user_id>`
  - GSI2SK = `PROVIDER#<provider>#EVENT#<provider_event_id>`

**Fields:** (Store full KairosCalendarEvent as serialized JSON plus select denormalized fields)
- All KCNF fields (see section 4.2)
- `provider_version` (str, always present) — unified version guard for concurrency (see section 4.2)
- `provider_etag` (Google) / `provider_change_key` (Microsoft) — preserved for reference
- `ingested_at`, `last_modified_at`
- `recurrence` (RecurrenceInfo) — for recurring event handling

**Normalizer contract:** MUST set `provider_version` for every event. Downstream concurrency guards MUST use `provider_version` only.

**Item size guard (REQUIRED — P0 prevents ingestion failures):**

DynamoDB items MUST be < 400KB. To prevent ingestion failures from large provider payloads:

**Truncation rules:**
- **Description:** Truncate to **8KB max**; set `description_truncated=true` if truncated
- **Attendees:** Cap at **first 200 attendees**; set `attendees_truncated=true` if truncated
- **Store hash:** Always store `raw_description_sha256` for change detection even when truncated

**Priority fields (MUST preserve):**
- `title`, `start`, `end`, `status`
- `organizer.email` (for invite detection)
- All `attendees[].email` (for entity linking, even if truncated to 200)
- `conference.join_url` (for briefings)
- `recurrence` identifiers (series_id, instance_id, original_start)

**Emergency trimming:**
If item still exceeds 400KB after truncation: drop lossy fields in order (location, truncated description) and log `kcnf_item_trimmed=true`.

**Why:** Provider descriptions can be huge (meeting notes, embedded images as data URLs, 500+ attendees). Without guards, ingestion fails → dropped events → broken briefings/invites/debriefs.

**Retention policy:**
- **TTL:** 180 days from `end` timestamp (events older than 6 months auto-deleted)
- **Rationale:** MVP does not require long-term calendar history; briefings/actions reference recent context only (<90 days)
- **Exception:** Kairos debrief events (`is_debrief_event=true`) MAY have longer retention (365 days) for audit trail
- **Post-MVP:** If analytics/search require history, archive to S3 before TTL expiry

**Update pattern when start_time changes (strengthened for concurrency safety):**

**MUST use TransactWriteItems for atomicity (VALID DynamoDB pattern):**

**Goal:** Move event when start_time changes (old SK → new SK) while mitigating GSI lag via redirect.

**Transaction strategy:**
1. Put the new event item at new SK (guard: must not already exist)
2. Update the old item IN PLACE to become a redirect tombstone (no Delete in txn)

**Why this pattern:** DynamoDB transactions disallow multiple operations on the same (PK, SK) in one transaction. The previous pattern (Delete old + Put tombstone at same key) is INVALID and will fail at runtime.

```python
dynamodb.transact_write_items(
    TransactItems=[
        {
            "Put": {
                "TableName": "kairos-calendar-events",
                "Item": {
                    **new_event_item,
                    "item_type": "event",
                    "ttl": new_ttl_epoch,
                },
                "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            }
        },
        {
            "Update": {
                "TableName": "kairos-calendar-events",
                "Key": {"PK": f"USER#{user_id}", "SK": old_sk},
                "ConditionExpression": (
                    "attribute_exists(PK) AND item_type = :event_type AND "
                    "provider_version = :provider_version"
                ),
                "UpdateExpression": (
                    "SET item_type = :redirect_type, redirect_to_sk = :new_sk, #ttl = :ttl "
                    "REMOVE title, description, #location, attendees, organizer, conference, recurrence"
                ),
                "ExpressionAttributeNames": {
                    "#ttl": "ttl",
                    "#location": "location"
                },
                "ExpressionAttributeValues": {
                    ":event_type": "event",
                    ":redirect_type": "redirect",
                    ":new_sk": new_sk,
                    ":ttl": int(time.time()) + 3600,  # 1 hour
                    ":provider_version": old_provider_version,
                },
            }
        },
    ]
)
```

**Notes:**
- This avoids multiple operations on the same PK/SK in one transaction (DynamoDB requirement)
- Converting the old item to a redirect preserves a single lookup path during GSI propagation
- The REMOVE list is best-effort trimming to reduce item size and prevent accidental downstream use
- Use lowercase `ttl` attribute name (standard for DynamoDB TTL)
- Version guard on old item prevents race conditions (concurrent updates fail transaction)

**Tombstone redirect rationale:**
- GSI propagation can lag up to seconds
- Queries during this window find old SK, then follow redirect to new SK
- TTL cleans up automatically after GSI catches up
- Alternative: accept eventual consistency gap (simpler but risks stale reads)

**Tombstone item type (REQUIRED — prevents treating redirects as real events):**

All `kairos-calendar-events` items MUST include `item_type` field:
- `item_type="event"` for real events
- `item_type="redirect"` for tombstones (has `redirect_to_sk`, `ttl`)

**Repository behavior (contract for all selectors):**

**Provider event lookup (REQUIRED):** `get_by_provider_event_id(user_id, provider, provider_event_id)`

**Reason:** During start_time moves, BOTH the live event and the redirect tombstone can match GSI_PROVIDER_ID.

**Algorithm:**
1. Query GSI_PROVIDER_ID with:
   - GSI2PK = `USER#<user_id>`
   - GSI2SK = `PROVIDER#<provider>#EVENT#<provider_event_id>`
2. If any items have `item_type="event"`: select the canonical one:
   - If exactly one: return it
   - If >1: pick the one with `max(ingested_at)` and log `duplicate_provider_id_items` (data corruption warning)
3. Else if only redirect item(s) exist: pick one redirect and follow `redirect_to_sk` (bounded, see `get_event()` below)
4. If none: return None

```python
class CalendarEventsRepository:
    def get_by_provider_event_id(self, user_id: str, provider: str, provider_event_id: str) -> KairosCalendarEvent | None:
        """Get event by provider event ID. Handles redirects and duplicate items."""
        items = dynamodb.query(
            IndexName="GSI_PROVIDER_ID",
            KeyConditionExpression="GSI2PK = :pk AND GSI2SK = :sk",
            ExpressionAttributeValues={
                ":pk": f"USER#{user_id}",
                ":sk": f"PROVIDER#{provider}#EVENT#{provider_event_id}"
            }
        )
        
        # Prefer event items over redirects
        event_items = [i for i in items if i.get('item_type') == 'event']
        if event_items:
            if len(event_items) == 1:
                return self._deserialize(event_items[0])
            # Multiple event items (data corruption): pick newest
            canonical = max(event_items, key=lambda x: x.get('ingested_at', ''))
            logger.warning(f"duplicate_provider_id_items: user_id={user_id}, provider={provider}, event_id={provider_event_id}, count={len(event_items)}")
            return self._deserialize(canonical)
        
        # Only redirects: follow one
        redirect_items = [i for i in items if i.get('item_type') == 'redirect']
        if redirect_items:
            return self.get_event(user_id, redirect_items[0]['redirect_to_sk'])
        
        return None
    
    def get_event(self, user_id: str, sk: str, *, max_redirect_hops: int = 2) -> KairosCalendarEvent | None:
        """Get single event by PK/SK. Follows redirects with bounded hop limit and loop detection."""
        visited = set()
        current_sk = sk
        
        for _ in range(max_redirect_hops + 1):
            # Detect redirect loops (data corruption safeguard)
            if current_sk in visited:
                raise RedirectLoopError(user_id=user_id, sk=current_sk)
            visited.add(current_sk)
            
            item = dynamodb.get_item(PK=f'USER#{user_id}', SK=current_sk)
            if not item:
                return None
            if item.get('item_type') != 'redirect':
                return self._deserialize(item)
            
            # Follow redirect
            current_sk = item['redirect_to_sk']
        
        # Exceeded hop limit (data corruption or excessive chaining)
        raise RedirectHopLimitError(user_id=user_id, sk=sk, limit=max_redirect_hops)
    
    def list_events_by_day(self, user_id: str, date: str) -> list[KairosCalendarEvent]:
        """Query GSI_DAY. Returns ONLY real events (filters out tombstones)."""
        items = dynamodb.query(GSI1PK=f'USER#{user_id}#DAY#{date}')
        return [self._deserialize(i) for i in items if i.get('item_type') == 'event']
```

**Why:** Prevents briefing scheduler or invite triage from accidentally processing tombstone items as real meetings.

**Version guard prevents race conditions:**
- If another process updated the event between read and delete, condition fails
- Transaction rolls back entirely (no orphaned items)
- Caller must retry from fresh query

**Alternative SK design (simpler, consider for future):**
- Current: `EVT#<start_iso>#<provider>#<provider_event_id>` (requires Put+Update redirect on start_time change)
- Alternative: `EVT#<provider>#<provider_event_id>` (start_iso only in GSI_DAY SK)
  - **Pros:** Avoids Put+Update redirect complexity; simple UpdateItem for start_time changes
  - **Cons:** Cannot sort events chronologically on main table without GSI query
  - **Decision:** Keep current design for MVP (time-ordered iteration useful); revisit if Put+Update redirect causes operational issues

#### C) kairos-calendar-sync-state (NEW)

**Purpose:** store webhook subscription metadata and delta tokens (not OAuth refresh tokens).

**Keys:**
- PK: `USER#<user_id>#PROVIDER#<provider>`
- SK: `SYNC`

**Fields:**
- `subscription_id` (Graph) or `channel_id` (Google)
- `subscription_expiry`
- `delta_link` (Graph) or `sync_token` (Google)
- `last_sync_at`
- `error_state` (optional)
- **`client_state`** (Microsoft Graph only): UUID generated at subscription creation
  - **Generation:** `uuid.uuid4()` per subscription
  - **Storage:** Stored here alongside subscription_id
  - **Rotation:** Regenerate on subscription renewal (security best practice)
  - **Verification:** Compare against `clientState` field in every Graph notification; reject mismatches
  - **Purpose:** Prevents webhook spoofing attacks
- **`channel_token`** (Google Calendar only): Random secret for webhook verification
  - **Generation:** `secrets.token_urlsafe(32)` per channel (random, unguessable)
  - **Storage:** Stored here alongside channel_id (NEVER log this value)
  - **Rotation:** Regenerate on channel renewal
  - **Verification:** Compare against `X-Goog-Channel-Token` header in every Google notification; reject mismatches
  - **Purpose:** Prevents webhook spoofing attacks (Google does not provide HMAC signatures)

##### C.1 Routing Items (REQUIRED FOR O(1) WEBHOOK ROUTING — P0)

**Critical:** Store reverse lookup items in the SAME table for O(1) webhook routing without scans.

**Google channel route item:**
- PK: `GOOGLE#CHANNEL#<channel_id>`
- SK: `ROUTE`
- Fields: `user_id`, `provider="google"`, `provider_calendar_id`, `channel_expiry`, `channel_token` (secret)
- **Write rule:** Upsert transactionally with SYNC item on subscription create/renew
- **Read rule:** `GetItem(PK=GOOGLE#CHANNEL#<channel_id>, SK=ROUTE)` → O(1) user_id + channel_token lookup
- **Security:** Store `channel_token` directly (encrypted at rest by DynamoDB); use constant-time comparison on verification

**Microsoft subscription route item:**
- PK: `MS#SUB#<subscription_id>`
- SK: `ROUTE`
- Fields: `user_id`, `provider="microsoft"`, `subscription_expiry`, `client_state`, `previous_client_state`, `previous_client_state_expires`
- **Write rule:** Upsert transactionally with SYNC item on subscription create/renew
- **Read rule:** `GetItem(PK=MS#SUB#<subscription_id>, SK=ROUTE)` then verify clientState

**Why:** Prevents expensive queries/scans in webhook hot paths; enables early rejection of spoofed requests.

#### D) kairos-user-state (EXISTING, EXTEND)

**Purpose:** per-user fencing, counters, and feature flags (Slice 2/3 semantics). 

**Add:**
- `default_calendar_provider` (google/microsoft)
- `briefings_enabled` (bool, default: true)
- `briefings_lead_time_minutes` (int, default: 10, range: 5-30)
- `briefings_max_per_day` (int, default: 8)
- `briefings_count_today` (int, resets daily)
- `invite_triage_enabled` (bool, default: true)
- `invite_triage_count_hour` (int, sliding window)
- `last_invite_triage_at` (timestamp)
- `actions_enabled` (bool, default: true)
- `kg_disambiguation_enabled` (bool, default: false — opt-in)

**Feature flags for gradual rollout:**
- `kcnf_enabled` (bool) — shadow write vs full migration
- `outlook_enabled` (bool) — per-user Outlook integration toggle

Maintain existing daily counters/idempotency-related fields.

#### E) kairos-action-items (NEW)

**Purpose:** actions extracted from calls and SMS; NOT KG entities. 

**Keys:**
- PK: `USER#<user_id>`
- SK: `ACTION#<created_at_iso>#<uuid>`

**Fields:**
- `action_id` (uuid)
- `text` (canonical action statement)
- `owner` (one of: "user", entity_id, raw_string)
- `due_at` (optional, tz-aware)
- `status` (open/done/cancelled)
- `source` (debrief_call / sms)
- `source_meeting_ids` (optional list)
- `evidence` (optional):
  - `meeting_id`
  - `segment_id`
  - `quote`
  - `t0`, `t1`
- `created_at`, `updated_at`

**Index:**
- **GSI_OPEN:**
  - GSI1PK = `USER#<user_id>#STATUS#open`
  - GSI1SK = `<due_at_or_created_at>#<uuid>`

#### F) kairos-invite-decisions (NEW)

**Purpose:** store recommendations, user approvals, and execution audit.

**Keys:**
- PK: `USER#<user_id>`
- SK: `INVITE#<provider>#<provider_event_id>`

**Fields:**
- **`decision_version`** (int, starts at 1, increments on invalidation) — **CRITICAL for versioned state machine**
- `recommendation` (accept/decline/propose_new_time/ask_agenda/ignore)
- `reasons` (list of strings)
- `confidence` (0..1)
- `user_response` (approved/rejected/none)
- `user_response_version` (int) — which decision_version did user approve?
- `responded_at`
- `executed` (bool), `executed_at`
- `audit`:
  - `model`
  - `prompt_version`
  - `inputs_hash`
- **Staleness tracking:**
  - `event_snapshot`: JSON snapshot of KCNF event at recommendation time
  - `provider_version`: unified version identifier (stored from snapshot)
  - `recommendation_invalidated` (bool): set true if event changes materially
  - `invalidation_reason` (string): why invalidated

**Versioned state machine (CRITICAL for correctness):**
1. **On new invite:** `decision_version = 1`, send SMS with version embedded
2. **Idempotency key includes version:** `invite-sms:<user_id>#<provider>#<event_id>#v<decision_version>`
3. **On user approval:** Store `user_response_version = decision_version`
4. **On staleness detection:**
   - Increment `decision_version`, set `recommendation_invalidated = true`
   - Re-run triage with new `decision_version`
   - Send SMS: "Your approval for `<event>` (v1) is no longer valid..."
   - New idempotency key: `invite-sms:<user_id>#<provider>#<event_id>#v<decision_version>`
5. **On execution:** MUST verify `user_response_version == decision_version` (reject stale approvals)

**Staleness detection triggers:**
- Any change to: `start`, `end`, `title`, `organizer`, `attendees` (add/remove)
- Compare `provider_version` from KCNF vs stored snapshot (mismatch indicates material change)
- On material change: increment `decision_version`, set `recommendation_invalidated = true`, re-run triage

**Example flow:**
```
1. Invite arrives (v1): "Board meeting Mon 3PM"
2. Recommend ACCEPT (v1), user approves
3. Before execution: organizer changes time to Tue 4PM
4. Staleness detected → v2, send: "v1 approval voided. New: Tue 4PM. Recommend ACCEPT (v2)"
5. User approves v2
6. Execute ACCEPT (verify user_response_version == decision_version == 2)
```
## 6. Secrets & Configuration (SSM)

Slice 2 stores secrets in SSM (SecureString) fetched at runtime; extend this per user.

### 6.1 Source of Truth Contract (CRITICAL — prevents divergence)

**Problem:** Slice 4 proposes per-user profile in SSM, `kairos-users` table, AND `kairos-user-state` table. Without clear ownership, these diverge → wrong timezone/day computation, wrong routing, broken schedules.

**Solution: Single source of truth per field category**

| Field Category | Source of Truth | Caching Allowed | Write Path |
|----------------|----------------|-----------------|------------|
| **Secrets/tokens** | SSM only | 5-15 min (with refresh on 403) | Admin script → SSM |
| **User identity + routing** | `kairos-users` routing items (PHONE#, EMAIL#) | Never (O(1) reads) | Provisioning → PutItem with uniqueness check |
| **User profile** | `kairos-users` PROFILE item (timezone, preferred_prompt_time, primary_email, phone) | 15 min | Provisioning + user SMS commands → UpdateItem |
| **Operational state** | `kairos-user-state` (counters, feature flags, last_call_at, next_prompt_at) | Never (strong consistency) | Handlers → conditional UpdateItem |

**Lambda behavior rules:**
1. **Global operators** (renewer, dispatcher): Read profile from `kairos-users` PROFILE; cache 15 min
2. **Per-user handlers** (webhooks, SMS): Read routing from `kairos-users` routing items (no cache); read state from `kairos-user-state` (no cache)
3. **Secrets**: All Lambdas read tokens from SSM; cache 5-15 min; refresh on OAuth 403 errors
4. **Writes**: MUST go to source-of-truth table; no caching for writes

**Why P0:** Prevents correctness bugs (wrong timezone → wrong day → missed briefings) and avoids near-term refactor.

### 6.2 Recommended SSM Parameter Layout

**Per-user profile:**
- `/kairos/users/<user_id>/phone-number`
- `/kairos/users/<user_id>/timezone`
- `/kairos/users/<user_id>/preferred-prompt-time`

**Google OAuth (global app creds + per-user refresh):**
- `/kairos/google/client-id`
- `/kairos/google/client-secret`
- `/kairos/users/<user_id>/google/refresh-token`

**Microsoft OAuth:**
- `/kairos/microsoft/client-id`
- `/kairos/microsoft/client-secret`
- `/kairos/microsoft/tenant-id` (optional)
- `/kairos/users/<user_id>/microsoft/refresh-token`

**Twilio:**
- `/kairos/twilio-account-sid`
- `/kairos/twilio-auth-token`
- `/kairos/twilio-phone-number`

**Bland / LLM keys (existing style):**
- `/kairos/bland-api-key`
- `/kairos/anthropic-api-key`
## 7. Interfaces (Handlers, Adapters, Protocols)

### 7.1 Lambda handlers

**Existing (extend for multi-user + new intents):**
- `calendar_webhook` (Google push -> normalize -> persist)
- `sms_webhook` (Twilio inbound -> intent routing)
- `daily_plan_prompt` (planner)
- `prompt_sender` (scheduled prompt)
- `initiate_daily_call` (if separate; currently merged into prompt_sender in Slice 2) 
- `webhook` (Bland call_ended -> transcript -> summary -> KG)  

**New:**
- `outlook_calendar_webhook` (Graph validation + notifications -> delta sync -> normalize -> persist)
- `pre_meeting_brief` (scheduled per meeting -> brief SMS)

**Optional split:**
- `invite_executor` (execute approved provider write; can also be handled inside sms_webhook)

### 7.2 Adapter / service interfaces (suggested)

**LLMClient (existing pattern; keep model-agnostic)**

```python
class LLMClient(Protocol):
    def complete(self, *, system: str, user: str, model: str, json_schema: dict | None = None) -> str:
        ...
```

**CalendarProviderClient (provider write operations)**

```python
class CalendarProviderClient(Protocol):
    def upsert_debrief_event(self, *, user_id: str, date: str, start_dt, end_dt, tags: dict) -> str:
        ...
    def delete_event(self, *, user_id: str, provider_event_id: str) -> None:
        ...
    def respond_to_invite(self, *, user_id: str, provider_event_id: str, response: str) -> None:
        ...
    def propose_new_time(self, *, user_id: str, provider_event_id: str, proposed_start, proposed_end) -> None:
        ...
```

**Normalizer**

```python
class CalendarNormalizer(Protocol):
    def normalize_google(self, *, user_id: str, event: dict) -> KairosCalendarEvent:
        ...
    def normalize_microsoft(self, *, user_id: str, event: dict) -> KairosCalendarEvent:
        ...
```

**Repositories**
- `UsersRepository`
- `CalendarEventsRepository` (KCNF)
- `ActionItemsRepository`
- `InviteDecisionsRepository`
- Existing Slice 3 repos remain unchanged 
## 8. Orchestration & Idempotency

### 8.1 Scheduling options

**Option A (simple ops): Dispatcher schedule**

- Run a single schedule every hour (or every 15 min).
- For each user, if local time matches planning window (e.g., 08:00), run daily plan.
- **Pros:** fewer schedules to manage. 
- **Cons:** more compute and "time math".

**Option B (parity with Slice 2): Per-user recurring schedules**

- One recurring schedule per user at 08:00 in user timezone.
- **Pros:** clearer; fewer wasted invocations. 
- **Cons:** schedule sprawl.

**MVP recommendation:**
- Choose Option B for predictability if pilot N is small (<= 50).
- Choose Option A if you expect many pilots quickly and want simpler ops.

### 8.2 One-time schedule naming (deterministic)

- Prompt schedule: `kairos-prompt-<user_id>-<YYYY-MM-DD>`
- Retry schedule: `kairos-retry-<user_id>-<YYYY-MM-DD>-<N>`
- Brief schedule: `kairos-brief-<user_id>-<provider>-<event_id_hash>`
  - **Improved hash (collision-resistant):**
    - `event_id_hash` = first **24 chars** of SHA256(`provider_event_id` + `start_iso`)
    - Includes `start_iso` for extra entropy (same event at different times = different hash)
    - 24 hex chars = 96 bits → collision probability ~1 in 10^14 per event
  - **Rationale:** EventBridge Scheduler name limit is 64 characters. Format: `kairos-brief-<uuid>-google-<24chars>` = ~60 chars
  - **Collision handling:** If schedule creation fails with ConflictException (name exists), append `-<random_4chars>` and retry once
  - **Debuggability:** Store full `provider_event_id` + `start_iso` in schedule tags/metadata for troubleshooting
- Reminder schedule: `kairos-remind-<user_id>-<action_id>`

**Hash quality verification (P0 test):**
- Generate 10,000 schedule names for random events → verify no collisions
- Verify determinism: same (event_id, start_iso) always produces same hash

### 8.3 Idempotency keys (include user_id always)

- prompt SMS send: `sms-send:<user_id>#<YYYY-MM-DD>`
- inbound SMS: `sms-in:<TwilioMessageSid>`
- daily call batch: `call-batch:<user_id>#<YYYY-MM-DD>`
- call retry: `call-retry:<user_id>#<YYYY-MM-DD>#<retry_number>`
- invite SMS: `invite-sms:<user_id>#<provider>#<event_id>#v<decision_version>`
- invite exec: `invite-exec:<user_id>#<provider>#<event_id>#v<decision_version>#<action>`
- **brief SMS (MAX 1 per logical meeting/day — STABLE IDEMPOTENCY):**
  - Key: `brief-sms:<user_id>#<provider>#<provider_event_id>#DAY#<YYYY-MM-DD-local>`
  - **Critical:** Does NOT include start_iso, so moving a meeting does NOT create new idempotency key
  - **Rationale:** Prevents duplicate briefings under reschedule churn (multiple time edits)
  - **Metadata:** Store `start_iso` used at send time in idempotency record for debugging
  - **Behavior:** If key exists, DO NOT send again even if meeting moved (schedule can still be rescheduled, but send is idempotent)
- reminder send: `action-remind:<user_id>#<action_id>#<due_iso>`

**Rule:** acquire idempotency before any external side effect.
## 9. Microsoft Graph (Outlook) Integration Strategy

### 9.1 Authentication (MVP)

- OAuth delegated permissions.
- **MVP acceptable:**
  - Manual refresh-token provisioning per user (admin script stores refresh token in SSM), or
  - Device code flow (CLI) that stores refresh token in SSM.

### 9.2 Subscriptions + delta sync

- Create a subscription to event changes.
- Webhook must support validation token handshake.
- Store subscription metadata in kairos-calendar-sync-state.
- **On notification:**
  - Use delta query with stored delta_link
  - Upsert returned events into kairos-calendar-events via normalizer
  - Update delta_link

### 9.3 Provider write operations (needed for Slice 4)

- Create/update Kairos debrief event with tags/extensions
- Respond to invites (accept/decline/tentative)
- (Optional) propose new time (can be "recommend-only" initially; execute later)
## 10. Multi-User Support (Thin Slice)

### 10.1 Provisioning (admin-script driven)

- Create user in kairos-users (phone, email, timezone, preferred prompt time)
- Store Google and/or Microsoft refresh tokens in SSM under per-user path
- Create provider webhook subscriptions and persist sync-state mappings

### 10.2 Routing rules (O(1) LOOKUPS — P0 FOR CORRECTNESS)

**Twilio inbound SMS (REQUIRED O(1) routing):**
- Normalize From phone number → E.164 format
- **GetItem(PK=PHONE#<e164>, SK=ROUTE)** → user_id (O(1) lookup, no scan)
- If item missing: return generic response "Number not recognized. Visit kairos.ai/signup to register." (do NOT reveal account existence)
- Enforce per-From rate limit (10/hour) BEFORE any additional lookups
- **Security:** Mandatory Twilio signature verification before GetItem

**Calendar webhooks (REQUIRED O(1) routing + verification):**

- **Google (REQUIRED channel token verification):**
  1. Parse `channel_id` from `X-Goog-Channel-ID` header
  2. Parse `token` from `X-Goog-Channel-Token` header
  3. **GetItem(PK=GOOGLE#CHANNEL#<channel_id>, SK=ROUTE)** → user_id + expected_channel_token (O(1))
  4. **Verify token** using constant-time comparison: `secrets.compare_digest(token, expected_channel_token)`
  5. If token mismatch or route missing: **reject immediately** (before any delta sync / KCNF writes)
  6. ONLY THEN proceed to delta sync and KCNF writes
  - **Why P0:** Google does NOT provide HMAC signatures (unlike Twilio); channel token is the only provider-supported authenticity check

- **Microsoft (REQUIRED clientState verification):**
  1. Parse `subscriptionId` from notification
  2. **GetItem(PK=MS#SUB#<subscription_id>, SK=ROUTE)** → user_id + client_state (O(1))
  3. **Verify clientState** (current or previous within 60-min overlap)
  4. If clientState mismatch or route missing: **reject immediately** (before expensive operations)
  5. ONLY THEN proceed to delta sync and KCNF writes

**Bland call webhook:**
- Ensure user_id is always included in Bland variables when call is initiated
- Webhook uses that user_id to route transcript/action/KG writes

### 10.3 Tenant isolation

- Every DDB read/write uses `USER#<user_id>` partitioning patterns (consistent with Slice 3). 
## 11. Action Extraction + Reminders

### 11.1 Core concept

Actions are assistant "to-dos" stored separately from the KG (no entity-resolution complexities). This matches Slice 3 design philosophy. 

### 11.2 Action extraction requirements

- **Must be grounded:**
  - For debrief calls: store transcript segment_id and quote with timestamps when possible. 
- **Normalize:**
  - `text`: canonical statement ("Email Sarah the updated deck")
  - `owner`: user / entity_id / raw string
  - `due_at`: optional
- **Conservative extraction:**
  - Only capture explicit commitments or explicit user instructions.

### 11.3 Pipeline placement

- **Post-call webhook pipeline:**
  - After transcript is stored
  - Run action extraction best-effort
  - Failures must not block summary delivery (graceful degradation) 

### 11.4 Reminders

- **If action has due_at:**
  - Schedule one-time reminder via EventBridge Scheduler
  - Send SMS with action text + quick reply "DONE"
- **If due_at is missing:**
  - Optionally ask a follow-up SMS: "When should I remind you?" (MVP optional)

### 11.5 SMS intents (extend existing LLM-based classifier)

**Add intents:**
- `ADD_ACTION`: "Remind me to…"
- `SET_REMINDER`: "Tomorrow at 9 remind me…"
- `LIST_ACTIONS`: "What are my open actions?"
- `MARK_DONE`: "Done" / "Mark X done"
- `INVITE_DECISION`: "Accept" / "Decline" / "Ask agenda" / "Propose" referencing last invite
## 12. Invite Handling (Recommend-only MVP)

### 12.1 Detection

Treat KCNF event as "invite candidate" if:

- organizer != user, OR
- response status indicates needsAction, OR
- event includes user as attendee and there is no existing invite decision record

### 12.2 Recommendation output

- `recommendation`: accept / decline / propose_new_time / ask_agenda / ignore
- `reasons`: list of short, user-readable reasons
- `confidence`: float 0..1

### 12.3 Signals (conservative)

- Organizer allowlist/denylist (config per user)
- Relationship strength from KG when available (recent mentions/edges) 
- Event overlaps with existing commitments
- Meeting size / optionality
- Missing join URL / missing agenda
- Title keywords (board, 1:1, investor, hiring, etc.) — used cautiously

### 12.4 SMS approval workflow

**Message template:**
- "Invite: `<title>` from `<organizer>` at `<time>`. Recommend: `<ACTION>` (reason1; reason2). Reply: ACCEPT / DECLINE / PROPOSE / ASK AGENDA / IGNORE."

**Persist:**
- Write recommendation record to kairos-invite-decisions
- On user reply, update user_response and store `user_response_version`
- **MVP: do NOT execute provider writes**
- **Phase 4H+:** Execute only when approved AND `user_response_version == decision_version`

### 12.5 Safety constraints

- MVP: Never execute provider writes (recommend-only). Phase 4H+: Execute only with explicit approval + version check.
- If event changes materially (time/title/organizer/attendees), invalidate prior recommendation and re-recommend.
- Rate-limit invite messages to avoid spamming the user.
## 13. Pre-Meeting Briefings

### 13.1 Scheduling policy

- Default: events with attendees (non-empty) and not all-day
- Fire at T-10 minutes (configurable)
- Quiet hours respected (per user timezone)
- Rate limit: max 8 briefings/day/user (MVP default; configurable)

### 13.2 Brief content (grounded)

- **Who:** organizer + attendees
- **Context:** last mention evidence / last summaries and linked action items
- **Suggested questions** (framed as suggestions; do not fabricate facts)
- **Join link**

### 13.3 User controls

Via SMS (or per-user config):
- `BRIEF ON` / `BRIEF OFF`
- `BRIEF 5` (change lead time) (optional)
- `BRIEF MAX 3` (optional)

## 14. Optional: KG Ambiguity Confirmation Loop

Slice 3 stores ambiguous mentions with candidates rather than forcing merges. 

Add a minimal confirmation loop:

- **Daily SMS:** "Quick check: Is 'Sam' = (1) Sam Johnson or (2) Sam Williams?"
- **On reply:**
  - mark mention linked
  - store user confirmation as evidence
  - optionally promote entity status to resolved (if policy supports)

This improves briefings and search quality over time with minimal effort.

## 15. Migration Strategy (Legacy meetings -> KCNF)

Current system stores meetings in kairos-meetings and uses it for "pending meetings" selection. 

**Migration plan:**

1. **Shadow write:**
   - Continue legacy writes
   - Also write normalized events into kairos-calendar-events
2. Build KCNF-based "today's events" selector using GSI_DAY.
3. Switch daily planner/prompt sender to use KCNF for meeting selection.
4. Keep legacy table for a deprecation window; then remove or keep as a projection if needed.

**Success criteria:**
- Identical "pending meetings" set produced by legacy and KCNF selectors for 2 weeks.
## 16. Implementation Plan (Phases / Checklists)

**Implementation order note (REVISED for reduced refactor risk + ship value early):**

**Sequencing rationale (CORRECTED — multi-user before provider-2):**
1. **4A (KCNF foundation):** Shadow mode de-risks migration, unlocks provider-agnostic downstream
2. **4B (Multi-user primitives):** Users table + O(1) routing items (phone, email, subscription/channel) — FOUNDATIONAL for all multi-user features; prevents P0 cross-tenant leaks
3. **4C (Microsoft Graph integration):** Outlook ingestion (read-only) with delta sync + clientState verification
4. **4D (Subscription renewal):** CRITICAL deploy immediately after 4C (Graph subscriptions expire in 3 days)
5. **4E (Actions + reminders):** SHIP VALUE FAST — calendar-independent, immediate "PA usefulness", minimal provider-write risk
6. **4F (Pre-meeting briefings):** High pilot value after routing + KCNF solid; requires duplicate suppression + stable idempotency
7. **4G (Invite triage recommend-only):** Learn recommendation quality + user preferences WITHOUT operational risk of calendar writes
8. **4H (Invite execution):** GATED — only after 4G proven in pilots (2+ weeks), versioned state machine tested
9. **4I (Microsoft Graph write path):** Debrief events + invite responses (only after read path proven)
10. **4J (KG disambiguation):** OPTIONAL — not blocking; MVP briefings use deterministic email matching
11. **4K (KCNF cutover):** Make KCNF primary after selectors match legacy for 1-2 weeks

**Critical path for pilot launch:** 4A → 4B → 4C → 4D → 4E → 4F (routing + actions + briefings provide core value)

### Phase 4A — KCNF foundation (normal form + storage)

- [ ] Define `KairosCalendarEvent` Pydantic model (section 4.2)
- [ ] Create `kairos-calendar-events` DDB table with GSI_DAY and GSI_PROVIDER_ID (fixed design)
- [ ] Implement `normalize_google_event()` -> KCNF
- [ ] Implement Put+Update redirect pattern for start_time changes (Section 5.2B)
- [ ] Update Google webhook ingestion to shadow-write KCNF (feature flag: `kcnf_enabled`)
- [ ] Implement `get_today_events(user_id, date)` using GSI_DAY
- [ ] Shadow-compare KCNF vs legacy meeting selection for 2 weeks
- [ ] Unit tests: normalizer field coverage, Put+Update redirect logic
- [ ] Deploy with `kcnf_enabled=false` (shadow mode)

### Phase 4B — Multi-user primitives (FOUNDATIONAL — MUST come before provider-2)

**Rationale:** Routing items + user registry are required for safe multi-user operation. This phase MUST complete before any Outlook integration to prevent cross-tenant routing failures.

- [ ] Create `kairos-users` table + `UsersRepository`
  - [ ] Add phone/email routing items (Section 10.2): `PHONE#<e164>`, `EMAIL#<normalized>`
  - [ ] Enforce uniqueness via `ConditionExpression: attribute_not_exists(PK) AND attribute_not_exists(SK)`
- [ ] Create `kairos-calendar-sync-state` table
  - [ ] Add subscription/channel routing items: `GOOGLE#CHANNEL#<id>`, `MS#SUB#<id>` (Section 10.2)
- [ ] Implement Twilio inbound routing: From phone → user_id mapping
  - [ ] Rate limiting: 10 SMS/hour/phone (enumeration prevention)
- [ ] Implement calendar webhook routing:
  - [ ] Google: channel_id → user_id + channel_token verification
  - [ ] Microsoft: subscription_id → user_id + clientState verification (prep for 4C)
- [ ] Update Bland webhook: ensure user_id in call variables; route transcript/KG writes
- [ ] Update Scheduler orchestration: choose Option A (dispatcher) or Option B (per-user schedules)
- [ ] Ensure all idempotency keys include user_id (audit existing Slice 1-3 code)
- [ ] Add feature flags to user state (section 5.2D)
- [ ] Unit tests: routing logic, user_id isolation, idempotency key format, phone enumeration protection
- [ ] Integration test: 2 users simultaneously (SMS routing, call routing, webhook routing)

### Phase 4C — Microsoft Graph (Outlook) integration (read-only ingestion)

**Rationale:** Outlook ingestion adds provider-2 diversity. Deploy AFTER multi-user routing (4B) is solid to avoid cross-tenant failures.

- [ ] Register Azure AD app; configure `Calendars.ReadWrite` delegated permissions
- [ ] Implement `MicrosoftGraphClient` adapter (token refresh, retries, exponential backoff)
- [ ] Implement `outlook_calendar_webhook` Lambda:
  - [ ] Validation token handshake
  - [ ] `clientState` generation (UUID), storage in sync-state, and verification on notifications
  - [ ] Change notification processing with early rejection (clientState verified BEFORE delta sync)
- [ ] Implement subscription creation logic with clientState + routing item write
- [ ] Implement delta sync with 410 Gone fallback to full sync
- [ ] Implement `normalize_microsoft_event()` -> KCNF
- [ ] Create `scripts/provision_outlook_user.py` (refresh token -> SSM)
- [ ] Unit tests: Graph client retries, normalizer, clientState verification, 410 Gone handling, early rejection path
- [ ] Integration test: validation handshake, clientState mismatch rejection (before expensive operations)

### Phase 4D — Subscription renewal automation (CRITICAL: Deploy immediately after 4C)

**Rationale:** Microsoft Graph subscriptions expire within 3 days. Without renewal, Outlook ingestion breaks.

- [ ] Implement `subscription_renewer` Lambda (scheduled hourly)
- [ ] Query `kairos-calendar-sync-state` for expiring subscriptions (expiry < 24h)
- [ ] Renew Google watch and Microsoft Graph subscriptions
  - [ ] Rotate Google `channel_token` on renewal (security best practice)
  - [ ] Rotate Microsoft `clientState` on renewal with 60-min overlap window
- [ ] Handle failures with exponential backoff (1min, 5min, 15min; max 3 retries)
- [ ] Mark `error_state` in sync-state and alert via CloudWatch on sustained failures
- [ ] Unit tests: renewal logic, backoff, grace period, clientState rotation, overlap window
- [ ] Integration test: simulate expiry, verify renewal
- [ ] **Deploy to production immediately after 4C deployment**

### Phase 4E — Action extraction + reminders (SHIP VALUE FAST, LOW RISK)

**Rationale:** Actions are calendar-independent, provide immediate "PA usefulness" value, and have minimal provider-write surface area. Ship before invite triage to de-risk and unblock pilot value.

- [ ] Create `kairos-action-items` table + `ActionItemsRepository`
- [ ] Implement `ActionExtractor`:
  - [ ] LLM-based extraction with Pydantic structured output
  - [ ] Evidence grounding: transcript segment_id + quote + timestamps
  - [ ] Conservative extraction: explicit commitments only
- [ ] Hook into post-call webhook pipeline (best-effort, graceful degradation)
- [ ] Extend SMS intents: `ADD_ACTION`, `SET_REMINDER`, `LIST_ACTIONS`, `MARK_DONE`
- [ ] Implement reminder scheduling (one-time EventBridge schedule)
- [ ] Implement idempotent reminder sending via SMS with "DONE" quick reply
- [ ] Unit tests: schema validation, evidence presence, reminder idempotency
- [ ] Integration test: action extraction -> reminder delivery -> mark done

### Phase 4F — Pre-meeting briefings (HIGH PILOT VALUE)

**Rationale:** Briefings provide high pilot-perceived value. Ship after routing + KCNF + basic ops are solid. Requires stable idempotency and duplicate suppression.

- [ ] Implement briefing scheduler (creates EventBridge one-time schedule at T - `briefings_lead_time_minutes`)
  (optionally schedule a few minutes earlier to allow LLM/SMS latency, but send-time checks enforce exact timing)
- [ ] Implement briefing generator:
  - [ ] KG query for meeting attendees → entities, relationships, recent mentions
  - [ ] LLM-based briefing generation (grounded in KG evidence)
  - [ ] Cost mitigation: prompt caching, cheaper model fallback (Haiku), prompt minimization
- [ ] Implement briefing SMS sender with **stable idempotency** (Section 8.3)
  - [ ] Key: `brief-sms:<user_id>#<provider>#<provider_event_id>#DAY#<YYYY-MM-DD-local>`
  - [ ] Belt & suspenders: re-check eligibility at send time (quiet hours, feature flag, rate limits)
- [ ] Implement duplicate suppression for multi-provider events (Section 4.5):
  - [ ] Compute `canonical_event_key` (title + organizer + start time + attendee hash)
  - [ ] Selector MUST dedupe by `canonical_event_key` before scheduling/sending
  - [ ] Suppress if same canonical event already briefed today
- [ ] Enforce rate limits: 8 briefings/day/user max
- [ ] Respect quiet hours: 22:00-07:00 local time (no sends)
- [ ] Unit tests: stable idempotency (reschedule does not create new key), duplicate suppression, rate limiting, quiet hours
- [ ] Integration test: schedule → send → idempotency prevents duplicate on reschedule

### Phase 4G — Invite triage (RECOMMEND-ONLY, NO PROVIDER WRITES)

**Rationale:** Prove recommendation quality and messaging before taking on operational risk of calendar writes. Learn from user approvals/rejections.

- [ ] Implement invite detection: query KCNF for `is_invite_candidate` events
- [ ] Implement `InviteTriage` service:
  - [ ] LLM-based recommendation (accept/decline/ask_agenda/ignore)
  - [ ] Compute reasons + confidence
  - [ ] Check organizer allowlist/denylist (user config)
  - [ ] Query KG for relationship strength
- [ ] Create `kairos-invite-decisions` table with `decision_version` (versioned state machine)
- [ ] Implement SMS recommendation workflow (extend `sms_webhook` intent: `INVITE_DECISION`)
- [ ] Implement staleness detection (section 5.2F): compare `provider_version` (KCNF) vs stored snapshot `provider_version`;
  mismatch triggers `decision_version` increment + re-recommendation
- [ ] **Implement user notification for voided approvals:**
  - [ ] On staleness detection post-approval: send SMS: "Your approval for `<event>` (v1) is no longer valid because `<reason>`. New recommendation (v2): `<new_rec>`. Reply to confirm."
  - [ ] Increment `decision_version`, send new recommendation
  - [ ] Log voided approvals in `kairos-invite-decisions` audit trail
- [ ] Implement rate limiting: 5/hour/user (burst: 3 in 5 min)
- [ ] Implement bulk-import detection: >20 new invites in 1 hour → pause triage, send SMS: "Bulk import detected. Enable invite triage? Reply YES"
- [ ] Unit tests: detection logic, versioned state machine, staleness detection, voided approval notification, rate limiting
- [ ] Integration test: event update after recommendation triggers version increment + invalidation
- [ ] **DO NOT implement provider execution yet** (deferred to Phase 4H)

### Phase 4H — Invite execution (EXPLICIT APPROVAL + PROVIDER WRITES, GATED ROLLOUT)

**Rationale:** Only after routing + idempotency + staleness/versioning tests pass in pilots. Requires operational readiness for provider writes.

**Prerequisites:**
- Phase 4G recommend-only running for 2+ weeks
- Approval→rejection rate >30% (learn from user feedback)
- No cross-tenant routing incidents
- Versioned state machine tested under production conditions

**Implementation:**
- [ ] Implement provider execution on approval (Google + Microsoft)
- [ ] **Version check (CRITICAL):** Execution MUST verify `user_response_version == decision_version` (reject stale approvals)
- [ ] Implement idempotency: `invite-exec:<user_id>#<provider>#<event_id>#v<decision_version>#<action>`
- [ ] Add execution audit trail: store `executed=true`, `executed_at`, execution errors
- [ ] Implement per-provider execution logic:
  - [ ] Google: `events.patch` or `events.update` with `sendUpdates=all`
  - [ ] Microsoft: PATCH `/me/events/<event_id>` with `responseStatus`
- [ ] Handle execution failures gracefully: retry with backoff, alert on sustained failures
- [ ] Unit tests: version check prevents stale execution, execution idempotency
- [ ] Integration test: full flow (recommend v1 → approve → stale → re-recommend v2 → approve → execute v2 only)
- [ ] **Guardrails:** Feature flag `invite_execution_enabled` per user; manual enablement only after pilot validation

### Phase 4I — Microsoft Graph write path (debrief events + invite responses)

**Rationale:** Provider writes for Outlook. Deploy ONLY after read path (4C) proven stable and after invite execution (4H) tested.

- [ ] Implement Outlook debrief event upsert with openExtensions tags
- [ ] Implement invite response operations: accept/decline/tentative via Microsoft Graph API
- [ ] Implement tagging rediscovery for both providers (parity test)
- [ ] Unit tests: tag round-trip (write + rediscover), error handling
- [ ] Integration test: create debrief in Outlook, verify tags persist

### Phase 4J — KG ambiguity confirmation loop (OPTIONAL — NOT BLOCKING FOR MVP)

**Note:** MVP briefings use deterministic attendee email matching + recent summaries. Disambiguation improves quality but is NOT required for pilot launch.

- [ ] Query ambiguous mentions via Slice 3 `GSI2_MENTION_BY_STATE` (state=`ambiguous`)
- [ ] Implement daily disambiguation prompt scheduler
- [ ] **Batching strategy (increased rate limit):**
  - [ ] Option A: Send 3-5 disambiguation prompts/day/user (sequential: one mention per SMS)
  - [ ] Option B: Batch multiple ambiguities into single SMS: "Quick check: (1) Is 'Sam' = Sam Johnson? (2) Is 'the CFO' = Jane Smith? Reply: 1A 2B"
  - [ ] **Recommendation:** Use Option A for MVP (simpler parsing); Option B for post-MVP efficiency
- [ ] Format SMS (Option A): "Quick check: Is 'Sam' = (1) Sam Johnson or (2) Sam Williams?"
- [ ] Extend SMS intent: `DISAMBIGUATE_MENTION`
- [ ] On user reply:
  - [ ] Mark mention as `linked` to chosen entity
  - [ ] Store user confirmation as evidence
  - [ ] Optionally promote entity status to `resolved`
- [ ] **Rate limit: 3-5 disambiguation SMS/day/user** (revised from 1/day; faster resolution)
- [ ] Unit tests: candidate formatting, reply parsing, batching logic (if Option B)
- [ ] Integration test: disambiguate -> verify mention linked

### Phase 4K — Migration cutover (KCNF becomes primary)

- [ ] Enable `kcnf_enabled=true` for all users
- [ ] Switch daily planner to use `get_today_events()` (KCNF source)
- [ ] Monitor for 1 week: verify no regressions in debrief flow
- [ ] Deprecate legacy `kairos-meetings` table (keep for 30 days, then delete)
## 17. Testing Strategy

### 17.1 Unit Tests (required)

**Normalizers (100% field coverage):**
- [ ] Google event → KCNF: all fields, edge cases (all-day, no attendees, no join link)
- [ ] Microsoft event → KCNF: field mapping parity
- [ ] Put+Update redirect pattern when start_time changes (with TransactWrite + version guard)
- [ ] Provider etag/changeKey preservation (for reference only; concurrency guards use `provider_version`)

**Recurring events (P0 — REQUIRED for MVP):**
- [ ] **Series master vs instance:** Normalizer correctly populates `recurrence.provider_series_id`, `provider_instance_id`, `is_recurring_instance`
- [ ] **Single-instance exception:** Modified instance has `is_exception=true`, `original_start` set, different `provider_instance_id`
- [ ] **Series time shift:** Update to series master changes ALL future instances (verify via delta sync)
- [ ] **Moved instance:** Single instance moved to different time creates exception (`is_exception=true`, `original_start` preserved)
- [ ] **Series + invite triage:** Invite response applies to correct target (series vs single instance) using `provider_instance_id`
- [ ] **Briefing + recurring:** Briefing scheduled for instance, not series master

**Timezone/day computation (P0 — correctness-critical):**
- [ ] **GSI_DAY determinism:** Same event + user always produces same GSI1PK regardless of normalizer invocation time
- [ ] **User local time:** Event at 2025-03-10T02:30:00Z, user in America/New_York → day = "2025-03-09" (verify with pytz/zoneinfo)
- [ ] **DST boundary handling:** Event during "spring forward" (2AM→3AM) correctly assigned to day pre-DST
- [ ] **All-day events:** Computed in user local time, not UTC
- [ ] **Multi-timezone users:** If user changes timezone in kairos-users, re-normalize affects future events only (no backfill)

**Multi-user routing:**
- [ ] Phone number → user_id mapping (success, not found, rate limit)
- [ ] Subscription/channel ID → user_id mapping via sync-state
- [ ] User_id isolation in all DDB queries

**Multi-provider per user (P0):**
- [ ] **Primary provider determination:** `default_calendar_provider` respected; fallback to most recent activity
- [ ] **Tag rediscovery:** Kairos-created debrief in Google has `is_debrief_event=true` after delta sync
- [ ] **Tag rediscovery:** Kairos-created debrief in Outlook has `is_debrief_event=true` after delta sync
- [ ] **Cross-provider duplicate detection:** Same event synced via both providers → suppress duplicate based on tags
- [ ] **Provider write path:** Daily planner creates debrief in primary provider only

**GSI_PROVIDER_ID lookup (P0 — prevents ingestion bugs):**
- [ ] **GSI_PROVIDER_ID returns {event, redirect}:** Repository returns event (not redirect)
- [ ] **GSI_PROVIDER_ID returns only redirect:** Repository follows redirect (bounded)
- [ ] **GSI_PROVIDER_ID returns multiple events:** Repository picks one with max(ingested_at) and logs warning

**Idempotency (acquire before side effect):**
- [ ] Prompt SMS: once/day/user (`sms-send:<user_id>#<date>`)
- [ ] Invite SMS: once/version (`invite-sms:<user_id>#<provider>#<event_id>#v<decision_version>`)
- [ ] Invite execution: once/action/version (`invite-exec:<user_id>#<provider>#<event_id>#v<decision_version>#<action>`)
- [ ] **Brief SMS: once/meeting/day (STABLE)** (`brief-sms:<user_id>#<provider>#<provider_event_id>#DAY#<YYYY-MM-DD-local>`)
  - **Critical:** Does NOT include start_iso → moving meeting does NOT create new idempotency key
- [ ] Reminder: once/action/due (`action-remind:<user_id>#<action_id>#<due_iso>`)

**Action extraction:**
- [ ] Pydantic schema validation (text, owner, due_at, status, evidence)
- [ ] Evidence presence when source=`debrief_call` (segment_id, quote, t0, t1)
- [ ] Conservative extraction: rejects vague/inferred actions

**Invite triage (versioned state machine — P0):**
- [ ] **Version increment:** Material change increments `decision_version`, sends new SMS with new idempotency key
- [ ] **Approval version tracking:** User approval stores `user_response_version = decision_version`
- [ ] **Execution version check:** Execution verifies `user_response_version == decision_version` (reject stale)
- [ ] **Idempotency key includes version:** `invite-sms:<user_id>#<provider>#<event_id>#v<version>` prevents double-send
- [ ] **Voided approval notification:** User notified when approval invalidated ("v1 approval voided, new: v2")
- [ ] Detection: organizer != user, response=needsAction
- [ ] Recommendation output schema (recommendation, reasons, confidence, decision_version)
- [ ] Staleness detection: material change in start/end/title/organizer/attendees
- [ ] Approval parsing: ACCEPT/DECLINE/PROPOSE/ASK AGENDA/IGNORE
- [ ] Rate limiting: 5/hour/user (burst: 3 in 5 min)

**Subscription management:**
- [ ] Renewal trigger: expiry < 24h
- [ ] Exponential backoff: 1min, 5min, 15min
- [ ] 410 Gone handling: fallback to full sync, re-establish delta_link
- [ ] **ClientState overlap window (P0 — reliability):**
  - [ ] On renewal: new clientState generated, previous stored with 60-min expiry
  - [ ] Notification with current clientState: accepted
  - [ ] Notification with previous clientState (within 60-min window): accepted
  - [ ] Notification with previous clientState (after 60-min expiry): rejected
  - [ ] Notification with unknown clientState: rejected immediately (early rejection path)

**Scheduler naming (deterministic):**
- [ ] Prompt: `kairos-prompt-<user_id>-<YYYY-MM-DD>`
- [ ] Retry: `kairos-retry-<user_id>-<YYYY-MM-DD>-<N>`
- [ ] Brief: `kairos-brief-<user_id>-<provider>-<event_id_hash>`
- [ ] Reminder: `kairos-remind-<user_id>-<action_id>`

**Feature flags:**
- [ ] `kcnf_enabled`: shadow write vs primary source
- [ ] `briefings_enabled`, `invite_triage_enabled`, `actions_enabled`: per-user toggles

### 17.2 Integration Tests (recommended)

**Microsoft Graph webhook:**
- [ ] Validation token handshake (initial subscription setup)
- [ ] clientState verification on notifications
- [ ] Reject spoofed requests

**Delta sync replay safety:**
- [ ] Duplicate notifications: idempotent upsert (no double-write)
- [ ] 410 Gone triggers full sync, re-establishes delta_link

**Multi-tenant isolation:**
- [ ] Two users simultaneously:
  - [ ] Correct SMS routing (phone → user_id)
  - [ ] Correct calendar routing (subscription → user_id)
  - [ ] No cross-user data leakage in DDB queries

**Invite staleness:**
- [ ] Event updated after recommendation sent:
  - [ ] Staleness detected (`provider_version` mismatch)
  - [ ] Recommendation invalidated
  - [ ] New recommendation generated
  - [ ] Old approval does NOT execute stale action

**Briefing quiet hours:**
- [ ] Briefing scheduled at 21:55 (fires)
- [ ] Briefing scheduled at 22:05 (skipped, quiet hours)
- [ ] Briefing scheduled at 06:55 (skipped, quiet hours)

**Action reminder flow:**
- [ ] Extract action from transcript → schedule reminder → SMS sent → user replies DONE → action marked done

**Subscription renewal:**
- [ ] Simulate expiry (set expiry to now+1h) → renewal Lambda runs → subscription renewed → new expiry set
- [ ] Simulate 410 Gone → full sync fallback → delta_link re-established

### 17.3 Load/Stress Tests (optional, pre-production)

- [ ] 50 users, 10 events/day each → verify GSI_DAY query performance
- [ ] Burst of 20 invites in 1 minute → verify rate limiting works
- [ ] Subscription renewal for 100 users simultaneously → no throttling

### 17.4 Testing Checklist Summary

**Critical paths:**
- [ ] Normalizer: Google → KCNF, Microsoft → KCNF (field coverage)
- [ ] Multi-user routing: phone → user_id, subscription/channel → user_id
- [ ] Idempotency: all side effects (SMS, calls, calendar writes, schedules)
- [ ] Event start_time change: Put+Update redirect pattern works (new item created, old item becomes redirect tombstone)
- [ ] Subscription renewal: expiry detection, renewal, 410 Gone handling
- [ ] Invite staleness: detect, invalidate, re-recommend
- [ ] Rate limiting: invites (5/hour), briefings (8/day), phone lookups (10/hour)
## 18. Observability / Safeguards

### 18.1 Observability

- CloudWatch alarms for all new Lambdas (errors, throttles), consistent with prior slices.  
- **Structured logs include:**
  - `user_id`, `provider`, `provider_event_id`, `schedule_name`, `idempotency_key`
  - `action_id`, `invite_decision_id` for traceability
- **Audit trails:**
  - invite recommendations and approvals in kairos-invite-decisions
  - actions include evidence pointers (segment_id, quote, timestamps)
  - subscription renewals logged with success/failure
- **Metrics:**
  - **Invite triage:** recommendations sent, approvals, executions, invalidations, voided approvals (staleness)
  - **Briefings:** sent, rate-limited, skipped (quiet hours, late), failed, rescheduled
  - **Actions:** extracted, reminded, marked done, expired (no response)
  - **KG disambiguation:** prompts sent, responses received, success rate (linked vs ignored), avg resolution time
  - **Subscription health:** renewal success rate, 410 Gone incidents, error_state count, renewal latency

### 18.1.1 Dashboards & Alarms (Global vs Per-User)

**Global operator dashboards (for system-wide health):**
- **Subscription Renewer:**
  - Renewal success rate (alarm if <95%)
  - Renewal latency (alarm if p99 >30s)
  - Error_state count (alarm if >2 users stuck)
  - 410 Gone incidents (alarm if >5/day)
- **Daily Plan Dispatcher (if used):**
  - Invocations per hour
  - Users processed per invocation
  - Failures (alarm if >5%)
- **Aggregate metrics:**
  - Total active users
  - Total events ingested/day
  - Total LLM calls/day, SMS sent/day, briefings sent/day

**Per-user health dashboards:**
- **User timeline (debug guidance):**
  - Correlation ID: `user_id` + `date` for daily flow
  - Key identifiers: `call_id`, `event_id`, `action_id`, `invite_decision_version`
  - Timeline view: prompt sent → reply → call started → call completed → summary sent → actions created
- **Per-user metrics:**
  - Last successful prompt (alarm if >48h)
  - Last successful call (alarm if >7 days)
  - Pending actions count (warn if >20)
  - Pending invites count (warn if >10)
  - Briefings sent today (check against rate limit)

**Replay tooling:**
- **Replay daily flow:** Re-run daily planner for specific user/date (idempotent)
- **Replay calendar sync:** Trigger full sync for user/provider (idempotent)
- **Replay briefing:** Re-create briefing schedule for specific event (check idempotency first)
- **Replay invite triage:** Re-run triage for specific event (increment decision_version)

### 18.2 Security Safeguards

**Google Calendar webhook verification (CRITICAL — P0 SECURITY):**
- **Do NOT rely on channel_id "secrecy"** (channel IDs can be guessed or leaked)
- **Primary control: channel token verification** (provider-supported authenticity check)
  - Set random `channel_token` when creating watch: `secrets.token_urlsafe(32)`
  - Google includes this in `X-Goog-Channel-Token` header on every notification
  - Verify using constant-time comparison: `secrets.compare_digest(received_token, expected_token)`
  - **Early rejection:** Verify token AFTER route GetItem, BEFORE any delta sync / KCNF writes
- **Why P0:** Google does NOT provide HMAC signatures; channel token is the only provider-supported verification method

**Microsoft Graph webhook validation (CRITICAL):**
- **Do NOT depend on signature verification** (Graph does not provide HMAC signatures like Twilio)
- **Primary controls:**
  - `validationToken` handshake (initial subscription setup only)
  - `clientState` verification on EVERY notification (MUST match stored value in sync-state)
- **Compensating controls for public Lambda URLs:**
  - Strict request schema validation (reject malformed JSON, missing required fields)
  - Request size limit: 256KB max (reject larger payloads immediately)
  - Rate limiting: 100 notifications/minute/subscription_id (alarm on excess)
  - Anomalous volume alarms: > 10x normal notification rate triggers alert
  - **Early rejection path (clarified):**
    - Allow exactly ONE O(1) DDB GetItem (subscription route) to obtain expected clientState
    - Verify clientState (current or previous within 60-min overlap)
    - Reject mismatches BEFORE any provider calls (delta sync), KCNF writes, or LLM/SMS work
  - Idempotency: duplicate notifications are safe (no double-processing)

**ClientState rotation with overlap window (MUST for reliability):**
- **Problem:** Rotating `clientState` on renewal can break in-flight notifications (Graph may send with old clientState for ~30-60 seconds)
- **Solution:** Accept `{current, previous}` clientState during overlap window:
  ```python
  # On renewal: generate new clientState, keep old for 60 minutes
  new_client_state = uuid.uuid4()
  sync_state['client_state'] = new_client_state
  sync_state['previous_client_state'] = old_client_state
  sync_state['previous_client_state_expires'] = now + timedelta(minutes=60)
  
  # On notification: accept if matches current OR (previous AND not expired)
  if client_state == sync_state['client_state']:
      # Valid
  elif (client_state == sync_state.get('previous_client_state') and 
        now < sync_state.get('previous_client_state_expires')):
      # Valid (in overlap window)
  else:
      # Reject
  ```

**SSM parameter access & IAM isolation (CLARIFIED):**
- **Per-user Lambdas** (sms_webhook, calendar_webhook per channel): IAM scoped to `/kairos/users/<user_id>/*`
- **Global operator Lambdas** (subscription_renewer, daily_plan_dispatcher): IAM MUST access `/kairos/users/*/` (all users)
  - **Clarification:** "Per-user IAM isolation" is ILLUSORY for global jobs
  - **Reality:** Trust Lambda code, not IAM, for multi-tenant isolation in global operators
  - **Mitigation:** Audit global operators carefully; minimize token access scope; log all SSM reads with user_id
- **Least-privilege principle:** Each Lambda only accesses keys it needs (e.g., sms_webhook does NOT need Google/Microsoft tokens)

**SMS inbound routing (HARDENED):**
- **Twilio signature verification:** MANDATORY for all inbound SMS (reject unsigned requests immediately)
- **Global throttling:** 1000 SMS/hour across all phone numbers (prevent DDoS via SMS)
- **Unknown number handling:**
  - Silent/generic response: "Number not recognized. Visit kairos.ai/signup to register."
  - Do NOT reveal user existence or error details
  - Log with rate-limited alert: > 50 unknown numbers/hour triggers investigation
- **Enumeration pattern detection:**
  - Alert if > 100 unique phones query in 1 hour
  - Alert if single phone queries > 10 different numbers in 1 hour (user_id fishing)

**Phone number enumeration prevention:**
- Rate limit phone → user_id lookups: **10 per phone per hour**
- Use CloudWatch metrics + Lambda@Edge or WAF if exposed via API Gateway
- Log failed lookups; alert on sustained patterns

**Action text sanitization:**
- If actions are ever rendered in UI/email: sanitize HTML, strip scripts
- For SMS: truncate at 500 chars to prevent SMS spam

**Logging hygiene (PII protection):**
- **NEVER log:**
  - Refresh tokens (OAuth tokens)
  - Full phone numbers (log last 4 digits only: `+1***1234`)
  - Event descriptions (may contain PII/sensitive content)
- **Redact/hash:**
  - Emails: log domain only (`@example.com`) or hash
  - User-generated text (action descriptions): log length/hash only
- **Safe to log:**
  - user_id (internal UUID)
  - provider_event_id (opaque identifier)
  - Timestamps, counts, durations
  - Error codes, state transitions

### 18.3 Trust Safety

- **Invite handling:** recommend-only in MVP; never execute without explicit user approval
- **All provider writes:** behind explicit user approval (SMS reply)
- **Action extraction:** conservative; only explicit commitments captured
- **Briefing content:** grounded in stored evidence; no fabricated facts

### 18.4 Rate Limiting (enforced)

- **Invite triage SMS:** 5 per hour per user (burst: 3 in 5 min)
- **Briefings:** 8 per day per user
- **Quiet hours:** 22:00 - 07:00 local time (no briefings)
- **Phone lookups:** 10 per phone per hour

## 19. Cost Estimates (MVP with 10 pilot users)

### 19.1 DynamoDB

**New tables:**
- `kairos-users`: negligible (10 items, 1KB each)
- `kairos-calendar-events`: ~500 events/user/month × 10 users = 5K items/month
  - Avg item size: 5KB → 25MB storage → ~$0.006/month
  - Reads: 100K/month → $0.025/month (eventual consistency)
  - Writes: 10K/month → $0.0125/month
- `kairos-calendar-sync-state`: 20 items (2 providers × 10 users)
- `kairos-action-items`: ~50 actions/user/month × 10 users = 500 items/month
- `kairos-invite-decisions`: ~20 invites/user/month × 10 users = 200 items/month

**Estimated total DDB cost:** ~$0.05/month for pilot (negligible)

### 19.2 SMS (Twilio)

**Outbound SMS:**
- Daily prompts: 10 users × 30 days = 300 SMS
- Briefings: 8/day × 10 users × 30 days × 50% trigger rate = 1,200 SMS
- Invite triage: 5/hour × 10 users × 30 days × 10% trigger rate = 360 SMS
- Reminders: 50 actions × 50% reminder rate × 10 users = 250 SMS
- **Total:** ~2,100 SMS/month × $0.0079 = **$16.59/month**

### 19.3 Voice Calls (Bland AI) — **DOMINANT COST DRIVER**

**Debrief calls (from Slice 1-2, carried forward):**
- 10 users × 20 calls/month × 10 minutes/call = 2,000 minutes/month
- Bland pricing: ~$0.10/minute → **$200/month**

**Note:** Voice is the largest single cost component. Slice 4 does NOT introduce new voice costs but MUST NOT ignore this baseline.

### 19.4 LLM Calls (Anthropic)

**Sonnet 4 ($3/$15 per MTok in/out):**
- **Briefings (largest LLM line item):** 1,200 briefings × 1.5K in × 400 out = 1.8M in + 480K out → $5.40 + $7.20 = **$12.60**
  - **Mitigation strategies (post-MVP):**
    - Cache daily "attendee context blobs" (KG summary per person) → reuse across briefings → 30% token reduction
    - Cheaper model fallback: Use Haiku for briefings with <3 attendees, no recent history → 80% cost reduction on ~40% of briefings
    - Prompt minimization: Compress summaries, prioritize recent mentions only (<7 days) → 20% token reduction
  - **Trade-off:** Cheaper models may reduce quality; A/B test required
- Action extraction: 10 users × 20 calls/month × 2K in × 500 out = 400K in + 100K out → $1.20 + $1.50 = $2.70
- **Invite triage (volume assumption revised):** 
  - **Optimistic assumption:** 10 users × 20 invites/month = 200 invites (assumes steady-state)
  - **Reality:** Initial import + recurring churn can spike to 100 invites/user/month in first week
  - **Revised:** 200 invites/month steady-state, 1000 invites one-time import
    - Steady-state: 200 × 1K in × 300 out = 200K in + 60K out → $0.60 + $0.90 = $1.50/month
    - One-time import: 1000 × 1K in × 300 out = 1M in + 300K out → $3.00 + $4.50 = $7.50
  - **Protections:**
    - Rate-limit recommendation generation: 10 invites/hour/user (prevent bulk-import spike)
    - Detect bulk-import mode: >20 new invites in 1 hour → pause triage, send SMS: "Bulk import detected. Enable invite triage? Reply YES"
    - Suppress triage during first sync unless user opted in explicitly
- KG disambiguation: 10 users × 3 prompts/day × 30 days × 500 in × 100 out = 450K in + 90K out → $1.35 + $1.35 = $2.70

**Haiku (for faster tasks, $0.25/$1.25 per MTok):**
- SMS intent classification: 500 SMS × 200 in × 50 out = 100K in + 25K out → $0.025 + $0.031 = $0.06

**Subtotal (steady-state):** $19.56/month

**Buffer (30% for retries, longer transcripts, edge cases, one-time import):** +$8.00

**Estimated total LLM cost:** ~$28/month for pilot (includes import spike buffer)

### 19.4 Lambda + EventBridge

- Lambda: negligible (free tier covers pilot)
- EventBridge Scheduler: ~10K invocations/month (recurring + one-time) → free tier

### 19.5 Total MVP Cost (10 users)

| Component | Monthly Cost | % of Total |
|-----------|--------------|------------|
| **Voice (Bland)** | **~$200** | **75%** |
| LLM (Anthropic) | ~$28 | 10% |
| SMS (Twilio) | ~$17 | 6% |
| DynamoDB | ~$0.05 | <1% |
| Lambda + EventBridge | ~$0 (free tier) | <1% |
| **Total** | **~$245/month** | **100%** |

**Per-user cost:** ~**$24.50/user/month** (dominated by voice)

**Cost breakdown insights:**
- **Voice is 75% of total cost** (from Slice 1-2 debrief calls)
- Slice 4 additions (briefings, invite triage, actions) add ~$20/month (8% increase)
- LLM cost is significant but not dominant

**Scaling:**
- Linear with user count up to ~100 users
- Beyond 100 users:
  - Bland volume discounts (negotiate after 100K minutes/month)
  - DDB autoscaling (negligible cost impact)
  - Twilio volume discounts
  - Haiku substitution for simpler tasks (briefings, SMS classification)
- At 100 users: estimated ~**$2,200-2,400/month** (~$22-24/user/month with modest economies of scale)

## 20. Runbook Stubs

### 20.1 Manual Calendar Re-Sync

**Scenario:** Delta link becomes stale (410 Gone) or subscription expires without renewal.

**Procedure:**
1. Identify affected user/provider from error logs
2. Run `scripts/force_full_sync.py --user-id <user_id> --provider <provider>`
3. Script performs full calendar list, normalizes events, upserts to KCNF
4. Updates `delta_link`/`sync_token` in `kairos-calendar-sync-state`
5. Verifies event count matches provider

**Recovery time:** ~2 minutes per user

### 20.2 User Provisioning

**Procedure:**
1. Create user record: `scripts/provision_user.py --email <email> --phone <phone> --timezone <tz>`
2. Store OAuth refresh token: `scripts/store_refresh_token.py --user-id <user_id> --provider google --token <token>`
3. Create calendar webhook subscription: `scripts/create_subscription.py --user-id <user_id> --provider google`
4. Create recurring daily planner schedule: `scripts/create_daily_schedule.py --user-id <user_id> --time 08:00`
5. Verify: trigger test debrief prompt

### 20.3 Debugging Stuck Invites

**Scenario:** User approved invite but execution didn't happen.

**Diagnosis:**
1. Query `kairos-invite-decisions` for user/provider/event
2. Check `executed` flag and `executed_at`
3. Check idempotency table for `invite-exec:<user_id>#<provider>#<event_id>#v<decision_version>#<action>`
4. Check Lambda logs for `outlook_calendar_webhook` or `sms_webhook` errors

**Manual execution:**
```bash
aws lambda invoke \
  --function-name kairos-invite-executor \
  --payload '{"user_id":"X","provider":"google","event_id":"Y","action":"accept"}' \
  out.json
```

### 20.4 Subscription Renewal Failure Recovery

**Scenario:** Renewal fails 3+ times consecutively; `error_state` set in sync-state table.

**Diagnosis:**
1. Check CloudWatch logs for `subscription_renewer` Lambda:
   ```bash
   aws logs tail /aws/lambda/kairos-subscription-renewer --since 1h --follow
   ```
2. Query `kairos-calendar-sync-state` for affected user/provider:
   ```python
   response = dynamodb.get_item(
       Key={'PK': f'USER#{user_id}#PROVIDER#{provider}', 'SK': 'SYNC'}
   )
   print(response['Item']['error_state'])  # error details
   ```
3. Verify OAuth token validity in SSM:
   ```bash
   aws ssm get-parameter --name /kairos/users/<user_id>/<provider>/refresh-token \
     --with-decryption
   ```
4. Check if subscription exists on provider side:
   - Google: `GET https://www.googleapis.com/calendar/v3/channels/stop`
   - Microsoft: `GET https://graph.microsoft.com/v1.0/subscriptions/<subscription_id>`

**Recovery steps:**

**If OAuth token expired:**
1. Re-run OAuth flow for user (manual or via provisioning script)
2. Update SSM parameter:
   ```bash
   scripts/store_refresh_token.py --user-id <user_id> --provider <provider> --token <new_token>
   ```
3. Clear error_state and retry

**If subscription deleted on provider side:**
1. Recreate subscription:
   ```bash
   scripts/recreate_subscription.py --user-id <user_id> --provider <provider>
   ```
2. Script will:
   - Create new subscription with new subscription_id and clientState (Graph)
   - Update `kairos-calendar-sync-state` with new metadata
   - Perform full sync to re-establish delta_link
3. Clear error_state in sync-state table:
   ```python
   dynamodb.update_item(
       Key={'PK': f'USER#{user_id}#PROVIDER#{provider}', 'SK': 'SYNC'},
       UpdateExpression='REMOVE error_state SET last_sync_at = :now',
       ExpressionAttributeValues={':now': datetime.utcnow().isoformat()}
   )
   ```

**If persistent failures (network/permissions):**
1. Check Lambda execution role permissions (Graph API, Calendar API)
2. Check VPC/security group rules if Lambda in VPC
3. Review CloudWatch Insights for patterns:
   ```
   fields @timestamp, @message
   | filter @message like /renewal failed/
   | stats count() by error_type
   ```
4. Escalate to on-call if issue persists > 6 hours

**Prevention:**
- CloudWatch alarm triggers after 2 consecutive renewal failures
- Subscription expiry grace period (24h) provides buffer for manual intervention

### 20.5 Moved Event Reconciliation

**Scenario:** Event moved to different time; start_time change requires Put+Update redirect pattern; queries may return stale data during GSI propagation.

**Symptoms:**
- User reports briefing sent for wrong meeting time
- Duplicate briefings for same event (old + new time)
- Query by GSI_DAY returns event at old time

**Diagnosis:**
1. Check `kairos-calendar-events` main table for event (by GSI_PROVIDER_ID)
2. Check if tombstone redirect exists at old PK/SK
3. Check GSI_DAY for both old and new day (if day changed)
4. Check briefing schedules for both old and new hash

**Resolution:**
- If tombstone exists: follow redirect, GSI will catch up within minutes
- If no tombstone and event missing: trigger manual delta sync for user/provider
- If duplicate briefings scheduled: manually delete old schedule via AWS CLI

**Prevention:**
- Tombstone redirect mitigates GSI lag (1-hour TTL)
- Belt & suspenders briefing checks prevent sending to moved/cancelled events

### 20.6 Invite Approval Invalidation

**Scenario:** User approved invite but event changed before execution; approval voided.

**Symptoms:**
- User complains: "I approved this meeting but nothing happened"
- User reports duplicate invite SMS for same meeting

**Diagnosis:**
1. Query `kairos-invite-decisions` for event
2. Check `decision_version` vs `user_response_version`
3. Check `recommendation_invalidated` flag
4. Review `invalidation_reason`
5. Check event history in KCNF (`provider_version` changes)

**Resolution:**
- If `user_response_version < decision_version`: user must re-approve current version
- If `recommendation_invalidated=true` but no notification sent: manually send SMS with new version
- If execution pending with stale approval: do NOT execute (version mismatch protection)

**Prevention:**
- Versioned state machine prevents stale execution
- Voided approval notifications inform user promptly

### 20.7 Briefing Reschedule Churn

**Scenario:** Meeting time changes repeatedly; briefings rescheduled multiple times; user receives late or duplicate briefings.

**Symptoms:**
- User reports: "Got 3 briefings for the same meeting"
- CloudWatch shows high briefing schedule create/delete rate

**Diagnosis:**
1. Check EventBridge Scheduler for event (search by event_id_hash)
2. Count schedules matching pattern `kairos-brief-<user_id>-<provider>-<hash>*`
3. Review event change history in KCNF (multiple start_time updates)
4. Check briefing idempotency table for sends

**Resolution:**
- Delete all schedules for event: `aws scheduler delete-schedule --name <name>`
- Recreate schedule for current meeting time
- If already sent: idempotency prevents duplicate send

**Prevention:**
- **Stable briefing idempotency:** Keys are per (provider_event_id, local_day), NOT per start_iso
  - Formula: `brief-sms:<user_id>#<provider>#<provider_event_id>#DAY#<YYYY-MM-DD-local>`
  - Reschedule churn within same day does NOT generate duplicate sends (key remains stable)
- Belt & suspenders checks prevent duplicate sends even if schedules duplicate

## 21. Key Patterns from Prior Slices

**SSM for secrets:**
- Per-user refresh tokens: `/kairos/users/<user_id>/<provider>/refresh-token`
- Global credentials: `/kairos/google/client-id`, `/kairos/twilio-account-sid`

**Idempotency table:**
- Table: `kairos-idempotency`
- Conditional writes: `PutItem` with `attribute_not_exists(pk)`
- TTL: 7 days for most keys, 90 days for audit trails

**Schedule naming (deterministic):**
- Recurring: `kairos-daily-plan-<user_id>`
- One-time: `kairos-prompt-<user_id>-<date>`, `kairos-retry-<user_id>-<date>-<N>`

**LLM client protocol:**
- Model-agnostic: `LLMClient` Protocol with `complete()` method
- Structured output: pass `json_schema` for Pydantic models
- Retry logic: exponential backoff on rate limits

**AI-first verification (Slice 3 pattern):**
- Extract → Verify → Compose pipeline
- Use LLM for semantic checks (entailment, consistency)
- Avoid brittle string matching; use confidence thresholds

**Graceful degradation:**
- New features (actions, invite triage, briefings) are best-effort
- Failures logged but do NOT block core debrief flow
- Feature flags for rollback capability

## 22. Open Questions (Decisions to lock)

1. **Scheduling strategy:**
   - **Decision:** Use **Option B (per-user recurring schedules)** for MVP (<= 50 users) for predictability
   - Switch to Option A (dispatcher) if scaling beyond 100 users
2. **Default provider for debrief event creation when user has both Google and Outlook:**
   - **Decision:** Configurable per user via `default_calendar_provider` in `kairos-user-state`
   - Fallback: use provider with most recent calendar activity
3. **Invite triage scope:**
   - **Decision MVP (Phase 4G):** `recommend-only` — show recommendations, collect user approvals, NO provider writes
   - **Decision post-pilot (Phase 4H):** `accept/decline/ask_agenda/ignore` execution — gated rollout after 2+ weeks of recommend-only validation
   - Defer `propose_new_time` to post-MVP (requires time-slot negotiation logic)
4. **Action boundary:**
   - **Decision:** Actions are extracted and reminded in Slice 4
   - Defer drafting follow-up emails to Slice 5 (requires email integration)

## 23. Success Criteria (Slice 4 "Done")

### 23.1 Platform

**KCNF as single source:**
- [ ] KCNF is the primary source for "today's events" selection (legacy table deprecated)
- [ ] GSI_DAY query returns correct events for user on given date
- [ ] GSI_PROVIDER_ID allows efficient lookup by provider event ID
- [ ] Put+Update redirect pattern works correctly for start_time changes (new item at new SK, old item becomes redirect tombstone with bounded follow)

**Outlook ingestion:**
- [ ] Subscriptions created successfully for all Outlook users
- [ ] Delta sync processes change notifications without duplicates
- [ ] 410 Gone fallback to full sync works reliably
- [ ] Subscription renewal runs hourly, renews within 24h grace period

**Multi-user support:**
- [ ] At least 2 pilot users running simultaneously with strict isolation
- [ ] Phone → user_id mapping works (Twilio inbound SMS routed correctly)
- [ ] Subscription/channel → user_id mapping works (calendar webhooks routed correctly)
- [ ] All idempotency keys include user_id
- [ ] No cross-user data leakage in logs or DDB queries

### 23.2 Assistant Capabilities

**Action extraction:**
- [ ] Actions extracted from debrief calls with evidence (segment_id, quote, timestamps)
- [ ] Actions stored in `kairos-action-items` table
- [ ] Reminders scheduled and delivered via SMS
- [ ] User can mark actions DONE via SMS reply
- [ ] Conservative extraction: only explicit commitments captured

**Invite triage (MVP recommend-only):**
- [ ] Incoming invites detected from KCNF events
- [ ] Recommendations generated with reasons + confidence
- [ ] SMS workflow captures user intent + stores `user_response_version`
- [ ] Staleness detection: invalidates recommendations when event changes materially
- [ ] Rate limiting enforced: 5 invite SMS/hour/user

**Invite execution (Phase 4H gated):**
- [ ] Provider execution only on explicit approval
- [ ] Execution verifies `user_response_version == decision_version`

**Pre-meeting briefings:**
- [ ] Briefings scheduled T-10 minutes before meetings
- [ ] Content grounded in KG (attendee entities, recent mentions, action items)
- [ ] Quiet hours respected (22:00 - 07:00 local time)
- [ ] Rate limiting enforced: 8 briefings/day/user
- [ ] User controls work: BRIEF ON/OFF, BRIEF <N>

**KG ambiguity confirmation (optional):**
- [ ] Daily disambiguation prompts sent for ambiguous mentions
- [ ] User replies link mentions to entities
- [ ] Evidence stored, entity status promoted to resolved

### 23.3 Reliability & Safety

**Idempotency:**
- [ ] No duplicated prompts under retries
- [ ] No duplicated calls under retries
- [ ] No duplicated invite responses under retries
- [ ] No duplicated briefings under retries
- [ ] No duplicated reminders under retries

**Graceful degradation:**
- [ ] Action extraction failures do NOT block summary delivery
- [ ] Invite triage failures do NOT block calendar sync
- [ ] Briefing failures do NOT block debrief flow
- [ ] All new features can be disabled via feature flags

**Security:**
- [ ] Microsoft Graph webhook validation (validationToken + clientState) enforced
- [ ] Phone number enumeration prevention: 10 lookups/hour/phone
- [ ] Action text sanitization if rendered in UI
- [ ] SSM parameter access: least-privilege IAM policies

**Observability:**
- [ ] CloudWatch alarms for all new Lambdas (errors, throttles)
- [ ] Structured logs include user_id, provider, event_id, idempotency_key
- [ ] Audit trails complete: invites (kairos-invite-decisions), actions (evidence pointers)
- [ ] Metrics tracked: invite triage rate, briefing rate, action extraction rate, subscription renewal success rate

### 23.4 Migration & Cutover

**Shadow write phase (1 week):**
- [ ] KCNF events shadow-written alongside legacy meetings table
- [ ] `get_today_events()` produces identical results as legacy selector

**Cutover phase (1 week):**
- [ ] `kcnf_enabled=true` for all users
- [ ] Daily planner switched to KCNF source
- [ ] No regressions in debrief flow (zero missed prompts/calls)
- [ ] Legacy table deprecated after 30-day retention window

### 23.5 Cost & Performance

**Cost targets (MVP pilot, 10 users, voice-dominant baseline):**
- [ ] Total: ~**$245/month** ±30% depending on call minutes (voice is ~75% = $200/month for 2,000 minutes)
- [ ] **Cost per user-week:** Target **<$7-10** depending on call length/duration
- [ ] Voice minutes: Primary lever (cap at 10-12 min/call soft limit; keep 1 call/day max policy)
- [ ] SMS cost: ~$17/month (secondary)
- [ ] LLM cost: ~$28/month (secondary, dominated by briefings)
- [ ] **Guardrails:**
  - Briefings: stable idempotency (max 1/meeting/day) + `briefings_max_per_day=8` enforced
  - Invite triage: bulk-import detection + rate limiting (5/hour, detect >20 invites/hour)
  - Voice: soft duration cap + 1 call/day max (already enforced in Slice 1-2)

**Performance targets:**
- [ ] GSI_DAY query latency: <100ms (p99)
- [ ] Normalizer latency: <50ms per event
- [ ] Invite triage recommendation: <2s end-to-end
- [ ] Briefing generation: <3s end-to-end

## 24. Evals & Measurement (POST-MVP / Pilot+)

**MVP measurement uses structured logs + CloudWatch metrics only.**
**Introduce `kairos-event-log` table only if pilots require durable querying beyond logs.**

### 24.1 Event Log Instrumentation (POST-MVP)

**Goal:** Compute weekly metrics to measure value and iterate quickly.

**Event types to log (DynamoDB `kairos-event-log` table — deferred to post-MVP):**

| Event Type | Required Fields | Purpose |
|------------|----------------|---------|
| `prompt_sent` | user_id, date, sent_at, channel (SMS) | Track prompt delivery |
| `prompt_reply` | user_id, date, reply, reply_at | Track user engagement |
| `call_started` | user_id, date, call_id, started_at | Track call initiation |
| `call_completed` | user_id, date, call_id, duration, ended_at | Track completion rate |
| `briefing_sent` | user_id, event_id, sent_at | Track briefing delivery |
| `briefing_skipped` | user_id, event_id, reason, skipped_at | Track suppression reasons |
| `action_created` | user_id, action_id, source, created_at | Track action extraction |
| `action_done` | user_id, action_id, done_at, days_open | Track completion |
| `invite_recommended` | user_id, event_id, recommendation, version, sent_at | Track triage |
| `invite_approved` | user_id, event_id, approval, version, approved_at | Track approval rate |
| `invite_executed` | user_id, event_id, executed_at | Track execution |
| `invite_invalidated` | user_id, event_id, old_version, new_version, reason | Track staleness |

**Retention:** 365 days for metrics analysis.

### 24.2 Value Measurement (Weekly Metrics)

**Debrief value metrics:**
- **Acceptance rate:** % of prompts that lead to completed calls
- **Time-to-start:** Median time from prompt sent to call started (target: <5 min)
- **Action yield:** Actions extracted per call (target: 1-3)
- **Action completion rate (7-day):** % of actions marked DONE within 7 days
- **Correction/complaint rate:** % of calls followed by user saying "wrong", "stop", "bad summary"
- **STOP/NO/ignore rates:** % of prompts ignored or explicitly declined
- **Cost per user-week:** Total cost / active users / 4 weeks (includes voice, LLM, SMS)

**Target KPIs (MVP baseline):**
- Acceptance rate: >60%
- Time-to-start: <10 min (p50)
- Action 7-day completion: >40%
- Correction/complaint: <5%
- **Cost per user-week: <$7-10** (aligned with $245/month total ÷ 10 users ÷ 4 weeks ≈ $6-7, allowing 30% buffer)

**Briefing value metrics:**
- **On-time rate:** % of briefings sent within T-10 ± 2 min window
- **Suppression reasons:** Count by reason (quiet hours, late, cancelled, rate limit)
- **Sampled usefulness rating:** Weekly SMS to 20% of users: "How helpful were briefings this week? 0-10" (sparse but directional)
- **Optional join-link CTR:** If briefing includes join link, track if user clicked (requires link shortener)

**Target KPIs (MVP baseline):**
- On-time rate: >95%
- Suppression for good reasons: >80% (quiet hours, cancelled meetings)
- Usefulness rating: >6/10 (p50)

**Invite triage value metrics:**
- **Recommendation→approval rate:** % of recommendations that user approves
- **Approval→execution rate:** % of approvals successfully executed (should be 100%, track failures)
- **Invalidation rate:** % of recommendations invalidated due to staleness
- **Time-to-decision:** Median time from recommendation sent to user approval

**Target KPIs (MVP baseline):**
- Recommendation→approval: >50% (learn from rejections)
- Approval→execution: >98%
- Invalidation rate: <10%

### 24.3 KG Accuracy & Trust Evals (Aligned to Extract→Verify)

**Grounding pass rate:**
- % of mentions with valid evidence pointers (segment_id exists, quote matches transcript)
- **Target:** 100% (deterministic verification should catch all errors)
- **Metric:** Weekly sample 50 random mentions, verify segment_id + quote

**Unsupported-edge rejection rate:**
- % of potential edges rejected by entailment verification
- **Target:** Conservative extraction should reject >50% of candidate edges (high precision)
- **Metric:** Log extraction attempts vs verified edges; compute rejection rate

**Ambiguity backlog trend:**
- Count of mentions in `ambiguous` state over time
- **Goal:** Decreasing (disambiguation prompts resolve backlog)
- **Metric:** Weekly snapshot of GSI2_MENTION_BY_STATE count

**Duplicate entity rate (especially by email):**
- % of entities with same email but different entity_id
- **Target:** <1% (deterministic email matching should prevent duplicates)
- **Metric:** Weekly query entities_by_email GSI, count duplicates

**Weekly manual audit sample:**
- Random sample 20 mentions/edges per week
- Human labels: "supported by transcript" vs "not supported" vs "uncertain"
- Compute precision: % labeled "supported"
- **Target precision:** >90%

### 24.4 MVP Experiment Plan (Optimization Thesis)

**Question:** Does Kairos provide value via (A) daily habit/peace of mind OR (B) high-leverage key meetings?

**Hypothesis A (Daily Habit):**
- Value comes from consistent daily debriefs, action tracking, peace of mind
- Success metric: High acceptance rate, low STOP rate, positive "net helpfulness" rating

**Hypothesis B (High Leverage):**
- Value comes from critical meeting briefings, important invite triage
- Success metric: High briefing usefulness rating, key decisions avoided/improved

**Experiment design (within-user crossover, 2-4 weeks):**

**Week 1-2: Baseline (full system)**
- All features enabled: daily prompts, briefings, invite triage, actions
- Measure all metrics above
- Weekly net helpfulness question: "Overall, how helpful was Kairos this week? 0-10"

**Week 3-4: Treatment (reduce daily habit)**
- Reduce daily prompts to 3×/week (Mon/Wed/Fri)
- Keep briefings, invite triage, actions
- Measure same metrics

**Week 5-6 (optional): Treatment (reduce briefings)**
- Restore daily prompts
- Reduce briefings to only "important" meetings (>3 attendees, recurring, or with join link)
- Measure same metrics

**Analysis:**
- Compare net helpfulness 0-10 across conditions
- Track annoyance guardrails: STOP rate, complaint rate
- Compute cost/user-week for each condition
- **Decision rule:** If full system net helpfulness >7 AND cost <$10/user-week → ship as-is; otherwise iterate

**Pilot size:** 10 users minimum (2-3 in each condition at a time)

**Duration:** 4-6 weeks total

## 25. Architectural Principles & Testing Priorities

### 24.1 Key Architectural Principles

**Provider abstraction:**
- All downstream logic uses KCNF only
- No Google/Microsoft branching outside normalizer layer
- Providers are interchangeable; logic is provider-agnostic

**Tenant isolation:**
- All DDB operations use `USER#<user_id>` partitioning
- No cross-user data access in queries, logs, or side effects
- Feature flags and rate limits per-user

**Idempotency:**
- Acquire idempotency key before any external side effect
- Keys always include user_id
- Use `kairos-idempotency` table with conditional writes
- TTL: 7 days for most keys, 90 days for audit trails

**Graceful degradation:**
- Action extraction, invite triage, briefings are best-effort
- Failures must not block core debrief flow
- Feature flags enable rollback capability
- Log errors but continue execution

**Security first:**
- Microsoft Graph `clientState` verification on every notification
- Rotate `clientState` on subscription renewal
- Phone number enumeration prevention (10 lookups/hour/phone)
- SSM parameter access: least-privilege IAM policies
- Action text sanitization if rendered in UI

**Operational excellence:**
- Event ID hashing (SHA256, first 24 hex chars) for schedule names (EventBridge 64-char limit)
- Store full provider_event_id in schedule tags for debuggability
- Subscription renewal with 24h grace period
- CloudWatch alarms on critical paths (renewal failures, rate limit violations)

### 24.2 Testing Priorities (Critical Path)

**P0 — Blocking for MVP (must pass):**

**Subscription management:**
- [ ] Expiry detection: subscription expires < 24h triggers renewal
- [ ] Renewal success: new expiry set, clientState rotated (Graph)
- [ ] 410 Gone handling: triggers full sync, re-establishes delta_link
- [ ] Failure recovery: 3 retries with exponential backoff (1min, 5min, 15min)
- [ ] Error state: marked in sync-state table, CloudWatch alarm fires
- [ ] Manual recovery: runbook tested end-to-end

**Security:**
- [ ] ClientState verification: Microsoft Graph notifications rejected if mismatch
- [ ] ClientState rotation: new UUID on renewal, old rejected
- [ ] Phone enumeration: 11th lookup in 1 hour returns rate limit error

**Invite staleness:**
- [ ] Material change detection: start/end/title/organizer/attendees change invalidates recommendation
- [ ] `provider_version` comparison: mismatch triggers re-recommendation
- [ ] Voided approval notification: user notified if approval voided post-change
- [ ] Stale approval blocked: execution prevented if recommendation_invalidated=true

**Multi-user isolation:**
- [ ] DDB queries: all use USER#<user_id> partition
- [ ] Cross-user leakage: user A cannot see user B's data (negative test)
- [ ] Logs: user_id included in all structured logs
- [ ] Side effects: SMS, calls, calendar writes routed correctly per user

**Put+Update redirect pattern:**
- [ ] Start time change: query GSI_PROVIDER_ID → Put new item at new SK → Update old item to redirect tombstone
- [ ] Atomicity: TransactWriteItems with version guard prevents race conditions
- [ ] GSI consistency: event queryable by provider ID and by day; redirect followed with bounded hops

**P1 — High Priority (MVP quality):**

**Rate limiting:**
- [ ] Invites: 5/hour enforced, 6th rejected; burst 3 in 5min
- [ ] Briefings: 8/day enforced, 9th skipped
- [ ] Phone lookups: 10/hour enforced, 11th rejected
- [ ] KG disambiguation: 3-5/day enforced (configurable)

**Briefing logic:**
- [ ] Quiet hours: 22:00-07:00 local time, briefings skipped
- [ ] Late briefings: meeting < now + lead_time, briefing skipped
- [ ] Rescheduling: user changes lead_time → pending briefings rescheduled
- [ ] Meeting time change: briefing rescheduled to new T-lead_time

**Idempotency:**
- [ ] SMS sends: duplicate SMS prevented under retries
- [ ] Call initiation: duplicate calls prevented
- [ ] Calendar writes: duplicate responses prevented (accept/decline)
- [ ] Schedule creation: duplicate schedules prevented (deterministic names)

**Graceful degradation:**
- [ ] Action extraction fails → summary still delivered
- [ ] Invite triage fails → calendar sync still works
- [ ] Briefing fails → debrief flow unaffected
- [ ] Feature flags: disable actions/invites/briefings without redeployment

**P2 — Medium Priority (post-MVP polish):**

**KG disambiguation:**
- [ ] Batching (Option B): multiple ambiguities in one SMS
- [ ] Response parsing: "1A 2B" correctly links two mentions
- [ ] Success rate metric: tracked and alarmed if < 50%

**Content quality:**
- [ ] Briefing grounding: all facts have KG evidence pointers
- [ ] Action extraction: conservative, only explicit commitments
- [ ] No hallucinations: reject ungrounded claims

**Performance:**
- [ ] GSI_DAY query: <100ms p99
- [ ] Normalizer: <50ms per event
- [ ] Invite triage: <2s end-to-end
- [ ] Briefing generation: <3s end-to-end 