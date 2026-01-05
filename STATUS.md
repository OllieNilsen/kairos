# Kairos Project Status

**Date:** 2025-01-05  
**Last Deployed:** ✅ Deployed (Slice 4A + 4B complete)

---

## 🚧 In Progress: Slice 4 - MVP Completion

### ✅ Phase 4A: KCNF Foundation (2025-01-05)

**Kairos Calendar Normal Form (KCNF)** - Provider-agnostic calendar event model

**Infrastructure:**
- ✅ Created `kairos-calendar-events` DynamoDB table with GSI_DAY and GSI_PROVIDER_ID
- ✅ Implemented Put+Update redirect pattern for start_time changes (atomic, with version guards)
- ✅ TTL auto-cleanup (180 days), item size guards (description 8KB, attendees 200)

**Models & Normalization:**
- ✅ `KairosCalendarEvent` Pydantic model with recurrence, organizer, attendees, conference info
- ✅ `normalize_google_event()` with field mapping, recurrence support, item size guards
- ✅ Provider version guards for concurrency control

**Repository:**
- ✅ `CalendarEventsRepository` with redirect-following logic
- ✅ Methods: `save_event()`, `update_event_start_time()`, `get_event()`, `get_by_provider_event_id()`, `list_events_by_day()`
- ✅ Redirect loop detection, hop limits, tombstone filtering

**Shadow-Write:**
- ✅ Integrated into `calendar_webhook` with `KCNF_ENABLED` feature flag (disabled by default)
- ✅ Graceful degradation (failures don't break webhook)

**Tests:** 26 new tests for normalizer + 20 for repository = 46 total

---

### ✅ Phase 4B: Multi-User Primitives (2025-01-05)

**Multi-tenant foundation with O(1) routing and security protections**

**Infrastructure:**
- ✅ Created `kairos-users` table with phone/email routing items
- ✅ Created `kairos-calendar-sync-state` table with webhook routing items
- ✅ O(1) GetItem lookups (no scans/GSIs in hot paths)

**Repositories:**
- ✅ `UsersRepository` with `get_user_by_phone()`, `get_user_by_email()`, `create_user()`
- ✅ Phone enumeration protection (10/hour rate limit - P0 security)
- ✅ Email normalization, atomic profile + routing item creation
- ✅ `CalendarSyncStateRepository` with channel/subscription routing
- ✅ Google channel token verification (constant-time comparison)
- ✅ Microsoft clientState verification with 60-min overlap window

**SMS Webhook Multi-User Routing:**
- ✅ Updated `sms_webhook` to use phone routing instead of hardcoded user_id
- ✅ Rate limiting enforced (prevents enumeration attacks)
- ✅ Graceful error handling for unregistered phones
- ✅ User isolation (different phones route to different users)

**Security (P0):**
- ✅ Phone enumeration rate limit (10/hour)
- ✅ Constant-time token comparison (prevents timing attacks)
- ✅ Minimal logging (only last 4 digits of phone numbers)
- ✅ User isolation with strict tenant boundaries

**Tests:** 24 new tests for repositories + 4 for SMS routing = 28 total

---

### 🔜 Phase 4C: Microsoft Graph Integration (Not Started)

**Planned:**
- [ ] Azure AD app registration
- [ ] `MicrosoftGraphClient` adapter with delta sync
- [ ] `normalize_microsoft_event()` → KCNF
- [ ] `outlook_calendar_webhook` Lambda handler
- [ ] validationToken handshake + clientState verification
- [ ] P0 tests for Graph client, normalizer, security

---

### 🔜 Phase 4D-4K: Additional MVP Features (Not Started)

- [ ] **4D:** Subscription renewal (Google + Microsoft)
- [ ] **4E:** Action items extraction + reminders
- [ ] **4F:** Pre-meeting briefings
- [ ] **4G:** Invite triage (recommend-only MVP)
- [ ] **4H:** Invite execution (gated, after 2+ weeks of 4G)
- [ ] **4I-4K:** See PLAN-SLICE4.md

---

## ✅ Completed: Slice 3 - Personal Knowledge Graph

### Implementation Summary (2025-01-04)

All Slice 3 phases complete with 100% unit test coverage:

**Phase 3A: Data Models & Infrastructure** ✅
- Created all Pydantic models (Entity, Mention, Edge, TranscriptSegment, etc.)
- Implemented AttendeeInfo with backward-compatible Meeting model upgrade
- Created 5 DynamoDB tables with GSIs (transcripts, entities, mentions, edges, entity-aliases)
- Added CDK resources with proper IAM permissions

**Phase 3B: Repository Layer** ✅  
- EntitiesRepository: CRUD operations, email lookup, alias management
- EdgesRepository: Dual-write pattern (EDGEOUT/EDGEIN) for efficient bidirectional queries
- MentionsRepository: State management (linked/ambiguous/new_entity_created)
- TranscriptsRepository: Segment storage and retrieval
- MeetingsRepository: Extended to support attendee_entity_ids

**Phase 3C: Entity Extraction** ✅
- EntityExtractor with LLM-based extraction (structured output via Anthropic tool use)
- Deterministic + LLM verification (quote grounding, segment validation)
- LLM entailment checking for relationship edges
- normalize_text() for robust text comparison
- AnthropicAdapter implementing LLMClient protocol (no model coupling)

**Phase 3D: Resolution Pipeline** ✅
- EntityResolutionService orchestrating full extraction → verification → resolution flow
- Candidate scoring and resolution (HIGH ≥ 0.85, LOW ≤ 0.30)
- Provisional entity creation for unmatched mentions
- Ambiguous mention handling (stored with candidates, no entity created)

**Phase 3E: Calendar Integration** ✅
- Auto-create resolved Person entities from calendar attendees (deterministic via email)
- Store entity IDs on Meeting records for quick reference
- Graceful degradation if entity creation fails

**Phase 3: Webhook Integration** ✅
- Transcript storage after successful calls
- Entity resolution pipeline triggered automatically
- Graceful error handling (failures don't break webhook)
- All environment variables and IAM permissions configured

### Test Coverage: 393 tests passing ✅
- Added 31 new tests for Slice 3 functionality
- 100% coverage of new modules and changes
- All deterministic (no real AWS, network, or LLM calls)

### Key Architecture Decisions
- **AI-first**: LLM-based verification, no brittle string matching
- **No model coupling**: LLMClient protocol, provider code isolated
- **Graceful degradation**: KG failures don't break existing workflows
- **Backward compatible**: Existing Slice 1-2 functionality unchanged

---

## ✅ Previously Completed

### Slice 2: Fixed-Time SMS Prompt Debriefs (196 tests)
- EventBridge scheduled prompts (08:00 Europe/London daily planner)
- Calendar-driven debrief events (user can move/delete)
- LLM-based SMS intent classification (AI-first approach)
- Call retry logic (max 3 retries, 15-min intervals)
- Post-call cleanup (mark meetings debriefed, delete calendar event)

### Slice 1: Mock Event Debrief (36 tests)
- Bland AI voice calls with deduplication
- Anthropic summarization
- SES email notifications
- CloudWatch alarms for all Lambdas

---

## 🔜 Future Enhancements

- [ ] Integration tests (optional but recommended)
- [ ] Twilio SMS prompting (when registration approved)
- [ ] Multi-user support

---

## 📊 Current Architecture

```
EventBridge (08:00 Europe/London)
    └── daily_plan_prompt Lambda
            ├── Creates/updates Google Calendar debrief event
            ├── Schedules one-time EventBridge trigger at preferred_prompt_time
            └── Resets daily state

EventBridge Scheduler (at preferred_prompt_time)
    └── prompt_sender Lambda
            ├── Checks idempotency & user state
            ├── Loads pending meetings from DynamoDB
            └── Initiates Bland AI call

Bland AI Call
    └── webhook Lambda (on call completion)
            ├── Detects success/failure
            ├── Schedules retry if unsuccessful (max 3, 15 min delay)
            ├── Saves transcript segments (Slice 3)
            ├── Extracts & resolves entities (Slice 3)
            ├── Summarizes transcript via Anthropic
            └── Sends SMS/email summary

Google Calendar Push
    └── calendar_webhook Lambda
            ├── Syncs meeting changes to kairos-meetings
            ├── Auto-creates entities for attendees (Slice 3)
            └── Detects debrief event moves/deletions
```

---

## 🧪 Test Commands

```bash
# Run all tests
make test

# Run linting
make lint

# Deploy
cd cdk && cdk deploy

# Trigger test call
python scripts/test_flow.py trigger-call

# Check status
python scripts/test_flow.py status
```

---

## 📝 Notes

- **498 tests passing** (Slices 1-3: 424 tests, +46 for Phase 4A, +28 for Phase 4B)
- Linting clean (ruff + mypy)
- Handler imports use try/except pattern for test compatibility
- User phone number stored in SSM: `/kairos/user-phone-number`
- Knowledge graph uses DynamoDB with GSIs (ready for Neptune migration later)
- AI-first approach: LLM-based verification throughout, no brittle heuristics
- **KCNF shadow-write disabled by default** (`KCNF_ENABLED=false` - flip to enable dual-write)

