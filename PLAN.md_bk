# Kairos Slice 1: Mock Event Debrief

## Architecture Overview

```mermaid
sequenceDiagram
    participant User as User (curl)
    participant TriggerLambda as Trigger Lambda
    participant SSM as AWS SSM
    participant BlandAI as Bland AI
    participant Phone as User Phone
    participant WebhookLambda as Webhook Lambda
    participant Anthropic as Anthropic API
    participant DynamoDB as DynamoDB
    participant SES as AWS SES

    User->>TriggerLambda: POST /trigger (JSON payload)
    TriggerLambda->>TriggerLambda: Validate payload (Pydantic)
    TriggerLambda->>SSM: GetParameter (Bland API key)
    SSM-->>TriggerLambda: API key (cached)
    TriggerLambda->>BlandAI: POST /v1/calls (initiate call)
    BlandAI-->>TriggerLambda: 200 OK (call_id)
    TriggerLambda-->>User: 202 Accepted (call_id)
    
    BlandAI->>Phone: Outbound Voice Call
    Phone->>BlandAI: Voice Conversation
    BlandAI->>BlandAI: Generate Transcript
    
    BlandAI->>WebhookLambda: POST /webhook (call_ended event)
    WebhookLambda->>WebhookLambda: Validate webhook (Pydantic)
    WebhookLambda->>DynamoDB: Check/Mark call_id (dedup)
    DynamoDB-->>WebhookLambda: OK (not duplicate)
    WebhookLambda->>SSM: GetParameter (Anthropic API key)
    SSM-->>WebhookLambda: API key (cached)
    WebhookLambda->>Anthropic: POST /messages (summarize)
    Anthropic-->>WebhookLambda: Summary text
    WebhookLambda->>SES: Send email summary
    SES->>User: Email Notification
    WebhookLambda-->>BlandAI: 200 OK
```

## Stack Decisions

| Component | Choice |
|-----------|--------|
| Voice AI | Bland AI |
| LLM | Anthropic API (Claude Sonnet 4) |
| IaC | AWS CDK (Python) |
| Runtime | Python 3.12 / ARM64 |
| Secrets | AWS SSM Parameter Store (SecureString, fetched at runtime) |
| Notification | AWS SES (email) - SMS planned for later |
| Deduplication | DynamoDB (conditional writes with TTL) |
| Monitoring | CloudWatch Alarms → SNS email alerts |

## Project Structure

```
kairos/
├── cdk/                          # Infrastructure as Code
│   ├── app.py                    # CDK app entry point
│   ├── kairos_stack.py           # Main stack definition
│   └── cdk.json                  # CDK config
├── src/
│   ├── core/                     # Domain logic (pure, no I/O)
│   │   ├── models.py             # Pydantic models
│   │   └── prompts.py            # System prompt templates
│   ├── adapters/                 # External service integrations
│   │   ├── bland.py              # Bland AI client
│   │   ├── anthropic_client.py   # Anthropic API client
│   │   ├── sns.py                # SNS publisher (reserved for SMS)
│   │   ├── ses.py                # SES email publisher
│   │   ├── ssm.py                # SSM Parameter Store (secrets)
│   │   └── dynamodb.py           # DynamoDB deduplicator
│   └── handlers/                 # Lambda entry points
│       ├── trigger.py            # POST /trigger handler
│       └── webhook.py            # POST /webhook handler
├── tests/
│   └── unit/                     # Unit tests (36 tests)
├── pyproject.toml                # Dependencies
└── Makefile                      # Build commands
```

## API Contracts

### Trigger Payload

```json
{
  "phone_number": "+15551234567",
  "event_context": {
    "event_type": "meeting_debrief",
    "subject": "Q4 Planning Session",
    "participants": ["Sarah Chen", "Mike Ross"],
    "duration_minutes": 45
  },
  "interview_prompts": [
    "What were the key decisions made?",
    "What are the action items and owners?"
  ]
}
```

### Bland AI Webhook Payload

```json
{
  "call_id": "uuid-here",
  "status": "completed",
  "to": "+15551234567",
  "from": "+18005551234",
  "duration": 332,
  "concatenated_transcript": "Assistant: Hi... User: Hey...",
  "variables": {"event_context": "{...}"}
}
```

## Implementation Checklist

### Phase 1: Setup ✅ COMPLETE
- [x] Initialize project structure
- [x] Create pyproject.toml with dependencies
- [x] Implement Pydantic models
- [x] Implement prompt builders
- [x] Create adapters (Bland, Anthropic, SNS, SSM)
- [x] Create Lambda handlers
- [x] Setup CDK stack
- [x] Create unit tests (13 tests passing)
- [x] Store secrets in SSM Parameter Store:
  ```bash
  aws ssm put-parameter --name "/kairos/bland-api-key" --value "sk-..." --type SecureString
  aws ssm put-parameter --name "/kairos/anthropic-api-key" --value "sk-ant-..." --type SecureString
  aws ssm put-parameter --name "/kairos/bland-webhook-secret" --value "whsec_..." --type SecureString
  aws ssm put-parameter --name "/kairos/my-email" --value "you@example.com" --type String
  ```

### Phase 2: Build & Deploy ✅ COMPLETE
- [x] Install dependencies: `uv pip install -e ".[dev,cdk]"`
- [x] Run tests: `make test` (13 passed)
- [x] Run linter: `make lint` (all checks passed)
- [x] Build Lambda layer: `make layer`
- [x] Bootstrap CDK: `cdk bootstrap`
- [x] Deploy: `make deploy`
- [x] Note the Function URLs from CloudFormation outputs

### Phase 3: End-to-End Test ✅ COMPLETE
- [x] Test trigger endpoint
- [x] Answer the phone call
- [x] Complete the debrief conversation
- [x] Verify email summary received (using SES for MVP)

### Phase 4: Hardening ✅ COMPLETE
- [x] Add DynamoDB for call_id deduplication (with TTL auto-cleanup)
- [x] Add CloudWatch Alarms for Lambda errors → SNS email alerts
- [x] Add Bland webhook HMAC-SHA256 signature verification
- [ ] Add SNS SMS as alternative to SES email (pending sandbox exit)

## Deployed Resources

| Resource | Name/ARN |
|----------|----------|
| Trigger Lambda | `kairos-trigger` |
| Webhook Lambda | `kairos-webhook` |
| DynamoDB Table | `kairos-call-dedup` |
| SNS Alarm Topic | `KairosAlarmTopic` |
| Lambda Layer | `KairosDepsLayer` |
| CloudWatch Alarms | `TriggerErrorAlarm`, `WebhookErrorAlarm` |

**Function URLs:** (get from `aws cloudformation describe-stacks --stack-name KairosStack`)
- TriggerUrl: `https://xxx.lambda-url.REGION.on.aws/`
- WebhookUrl: `https://xxx.lambda-url.REGION.on.aws/`

## Secrets Management

API keys are stored as **SecureString** in SSM Parameter Store and fetched at Lambda runtime (not injected as environment variables). This allows:
- Secret rotation without redeployment
- Proper encryption at rest
- IAM-based access control

The SSM adapter (`src/adapters/ssm.py`) uses LRU caching to avoid repeated API calls within a single invocation.

## Cost Estimate (Per Call)

| Service | Cost |
|---------|------|
| Bland AI | ~$0.09/min (est. 3 min = $0.27) |
| Anthropic | ~$0.003 |
| Lambda | < $0.001 |
| DynamoDB | < $0.001 |
| SES Email | < $0.001 |
| SNS SMS | $0.0075 (when enabled) |
| **Total** | **~$0.27 per debrief** |

## Quick Commands

```bash
# Setup
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,cdk]"

# Test & Lint
make test
make lint

# Deploy
make layer
make deploy

# Get Function URLs
aws cloudformation describe-stacks --stack-name KairosStack \
  --query "Stacks[0].Outputs" --output table

# Clean
make clean
```

---

# Slice 2: Fixed-Time SMS Prompt Debriefs (MVP)

## Overview

A simpler, predictable, low-annoyance approach to calendar-driven debriefs. Instead of complex gap detection with Step Functions, we use a fixed daily prompt time with event-driven SMS handling.

### MVP Policy (Single User)

- **One debrief call per day maximum**
- **Ask-first via SMS** at a fixed scheduled time (configurable), NOT gap detection
- **No reminders/nagging by default** - if user ignores SMS, do nothing until next day
- **If user replies NO** - snooze until tomorrow (unless they text READY later)
- **Inbound SMS can arrive anytime** (minutes/hours later) and must still work

### User Experience Flow

```
[8:00 AM] Daily planner runs
    → Calculates today's prompt time (e.g., 5:30 PM)
    → Creates/updates "Kairos Debrief" event in user's Google Calendar
    → Resets daily counters
    
[User sees calendar event]
    → Can move it to a different time (event has guestsCanModify: true)
    → Can delete it to skip today's debrief
    → Calendar webhook detects change → system reschedules prompt
    
[5:30 PM] Prompt sender checks
    → Reads debrief event time from calendar (in case user moved it)
    → "You had 3 meetings (Q4 Planning, 1:1 Sarah, Standup). Debrief call? Reply YES or NO"
    
[User replies anytime]
    → "yes" / "ok" / "ready" → Call initiates immediately
    → "no" / "skip" / "busy" → "OK, I'll check in tomorrow. Text READY if you change your mind."
    → "stop" → Opt out of all future prompts
    → [hours later] "ready" → Call still initiates (if no call made yet today)
    
[Call completes]
    → AI reviews each meeting sequentially
    → Summary SMS sent via Twilio
    → Meetings marked as debriefed
    → Debrief calendar event marked as completed/deleted
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Calendar sync | **Google Push (Webhooks)** | Real-time meeting updates (reuse from Phase 2A) |
| Debrief scheduling | **Calendar event (`guestsCanModify`)** | User can directly move event; webhook detects & reschedules |
| Orchestration | **EventBridge Scheduler** | One-time triggers at exact time; no polling |
| SMS (prompt + summary) | **Twilio** | Single channel for all notifications; immediate delivery |
| State management | **DynamoDB** | User state + idempotency in one place |
| Execution model | **Event-driven** | SMS webhook triggers call, no polling loops |
| Idempotency | **Conditional writes** | Prevent duplicate prompts/calls even with retries |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SLICE 2 MVP ARCHITECTURE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                        ┌─────────────────────────────────────────┐          │
│                        │            Google Calendar              │          │
│                        │  (meetings + debrief scheduling event)  │          │
│                        └────┬──────────────▲──────────────▲──────┘          │
│                             │push          │create        │read             │
│                             │              │event         │event            │
│                             ▼              │              │                 │
│                    ┌─────────────────┐     │              │                 │
│                    │ Calendar Webhook│     │              │                 │
│                    │ Lambda          │     │              │                 │
│                    └────────┬────────┘     │              │                 │
│                             │              │              │                 │
│  ┌──────────────┐           │         ┌────┴────────┐     │                 │
│  │ EventBridge  │──────────────────►  │ Daily Plan  │     │                 │
│  │ (8am daily)  │           │         │ Lambda      │     │                 │
│  └──────────────┘           │         └──────┬──────┘     │                 │
│                             │                │            │                 │
│                             ▼                ▼            │                 │
│                    ┌─────────────────────────────────┐    │                 │
│                    │          DynamoDB               │    │                 │
│                    │  (meetings + user-state)        │    │                 │
│                    └────────────────┬────────────────┘    │                 │
│                                     │                     │                 │
│                                     │                     │                 │
│  EventBridge Scheduler              │                     │                 │
│  (one-time at prompt time)          │                     │                 │
│         │                           │                     │                 │
│         ▼                           ▼                     │                 │
│  ┌─────────────────┐─────────────────────────────────────►┘                 │
│  │ Prompt Sender   │                                                        │
│  │ Lambda          │────►  Twilio (outbound SMS)                            │
│  └─────────────────┘                                                        │
│                                                                             │
│  Twilio ──────────►┌─────────────────┐────►┌─────────────────┐              │
│  (inbound SMS)     │ SMS Webhook     │     │ Initiate Call   │              │
│                    │ Lambda          │     │ Lambda          │              │
│                    └─────────────────┘     └────────┬────────┘              │
│                                                     │                       │
│                                                     ▼                       │
│                                            ┌─────────────────┐              │
│                                            │ Bland AI        │              │
│                                            │ (voice call)    │              │
│                                            └────────┬────────┘              │
│                                                     │                       │
│                                                     ▼                       │
│                                            ┌─────────────────┐              │
│                                            │ Webhook Lambda  │──► Twilio    │
│                                            │ (existing)      │   (SMS)      │
│                                            └─────────────────┘              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Calendar Interactions:**
- **Daily Plan Lambda** → Creates "📞 Kairos Debrief" event at preferred prompt time (with `guestsCanModify: true`)
- **Calendar Webhook** → Detects if user moves/deletes the debrief event → reschedules EventBridge
- **Prompt Sender Lambda** → Double-checks event time before sending (belt & suspenders)
- **Post-call** → Deletes or marks event as completed

## Data Model

### DynamoDB: `kairos-meetings` Table (existing)

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_id` | PK | User identifier |
| `meeting_id` | SK | Google Calendar event ID |
| `title` | String | Meeting subject |
| `start_time` | ISO8601 | Meeting start |
| `end_time` | ISO8601 | Meeting end |
| `attendees` | List | Participant names |
| `status` | String | `pending` / `debriefed` / `skipped` |
| `google_etag` | String | For sync conflict detection |
| `created_at` | ISO8601 | When synced |
| `ttl` | Number | Auto-cleanup after 30 days |

**GSI (future-proof):**
- GSI1PK = `user_id`
- GSI1SK = `start_time` (or `start_time#meeting_id`)

### DynamoDB: `kairos-user-state` Table

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_id` | PK | User identifier |
| **Contact Info** | | |
| `phone_number` | String | For SMS prompts + summaries (E.164) |
| `email` | String | Optional, for future use |
| `timezone` | String | e.g., "Europe/London" |
| **Scheduling** | | |
| `preferred_prompt_time` | String | "17:30" (HH:MM format) |
| `next_prompt_at` | ISO8601 | When to send today's prompt |
| `prompt_schedule_name` | String | EventBridge Scheduler schedule name |
| `debrief_event_id` | String | Google Calendar event ID for today's debrief |
| `debrief_event_etag` | String | For detecting user modifications |
| **Daily State** (reset each morning) | | |
| `prompts_sent_today` | Number | Counter (max 1) |
| `last_prompt_at` | ISO8601 | When last prompt was sent |
| `awaiting_reply` | Boolean | True after prompt sent |
| `active_prompt_id` | String | Current prompt identifier |
| `daily_call_made` | Boolean | True after call initiated |
| `last_call_at` | ISO8601 | When last call was made |
| `daily_batch_id` | String | `user_id#YYYY-MM-DD` |
| `last_daily_reset` | ISO8601 | When counters were reset |
| **Control State** | | |
| `snooze_until` | ISO8601 | Don't prompt/call until this time |
| `stopped` | Boolean | User opted out (STOP) |
| **Google OAuth** (existing) | | |
| `google_refresh_token` | String (encrypted) | OAuth refresh token |
| `google_channel_id` | String | Calendar push subscription ID |
| `google_channel_expiry` | ISO8601 | When to renew subscription |

### DynamoDB: `kairos-idempotency` Table

| Attribute | Type | Description |
|-----------|------|-------------|
| `idempotency_key` | PK | Unique operation key |
| `created_at` | ISO8601 | When acquired |
| `metadata` | Map | Optional context |
| `ttl` | Number | Auto-cleanup (7 days) |

**Key Formats:**
- SMS send dedup: `sms-send:{user_id}#{YYYY-MM-DD}`
- Inbound SMS dedup: `sms-in:{MessageSid}`
- Call batch dedup: `call-batch:{user_id}#{YYYY-MM-DD}`
- Daily lease: `daily-plan:{user_id}#{YYYY-MM-DD}`

## Budget & Safety Rules

### Hard Rules (enforced in code)

1. **If `stopped = true`** → Never prompt or call
2. **If `prompts_sent_today >= 1`** → Never prompt again today
3. **If `daily_call_made = true`** → Never call again today
4. **If `snooze_until` is in the future** → Don't prompt or call
5. **No reminders by default** → Single prompt per day, no follow-ups

### Intent Parsing

| User Says | Intent | Action |
|-----------|--------|--------|
| yes, yeah, yep, ok, okay, sure, call, go | `YES` | Initiate call |
| ready, i'm ready, now | `READY` | Clear snooze, initiate call |
| no, nope, nah, later, skip, busy | `NO` | Snooze until tomorrow |
| stop, unsubscribe, quit, cancel | `STOP` | Set `stopped = true`, never contact again |
| (anything else) | `UNKNOWN` | Reply with help message |

## New Project Structure (Additions)

```
kairos/
├── src/
│   ├── adapters/
│   │   ├── ... (existing)
│   │   ├── google_calendar.py    # OAuth + Calendar API (existing)
│   │   ├── twilio_sms.py         # Send SMS + validate webhook signature
│   │   ├── user_state.py         # DynamoDB user state repository
│   │   └── idempotency.py        # Dedup helpers (SMS, calls, leases)
│   ├── core/
│   │   ├── models.py             # Add UserState, TwilioInboundSMS models
│   │   └── prompts.py            # Add multi-meeting debrief prompt
│   ├── handlers/
│   │   ├── ... (existing)
│   │   ├── calendar_webhook.py   # Google push notifications (existing)
│   │   ├── daily_plan_prompt.py  # 8am daily planner
│   │   ├── prompt_sender.py      # Check time & send SMS prompt
│   │   ├── sms_webhook.py        # Twilio inbound SMS handler
│   │   └── initiate_daily_call.py # Start Bland call with all pending meetings
├── cdk/
│   └── kairos_stack.py           # Add new tables, Lambdas, schedules
```

## Implementation Phases

### Phase 2A: Google Calendar Integration ✅ COMPLETE
- [x] Create Google Cloud project & OAuth credentials
- [x] Implement `google_calendar.py` adapter (OAuth2 + Calendar API)
- [x] Create `calendar_webhook.py` Lambda handler
- [x] Create `kairos-meetings` DynamoDB table
- [x] Sync calendar events → DynamoDB on webhook
- [x] Handle webhook verification (Google challenge)
- [x] Setup calendar push subscription (watch)
- [x] Store refresh token in SSM (encrypted)
- [x] Add CDK resources (Lambda, DynamoDB, Function URL)

### Phase 2A.1: Debrief Event Change Detection (extends calendar webhook)
- [ ] Enhance `calendar_webhook.py` to detect changes to the debrief event:
  - Check if changed event ID matches `debrief_event_id` in user state
  - **If event moved** → update `next_prompt_at` and reschedule EventBridge
  - **If event deleted** → clear `debrief_event_id`, delete EventBridge schedule
  - **If event time is in the past** → skip (too late to reschedule)
- [ ] Grant calendar webhook permission to modify EventBridge Scheduler
- [ ] This enables real-time response to user calendar changes

### Phase 2B: User State & Idempotency Tables
- [ ] Create `kairos-user-state` DynamoDB table
- [ ] Create `kairos-idempotency` DynamoDB table
- [ ] Implement `user_state.py` adapter (read/update with conditionals)
- [ ] Implement `idempotency.py` adapter (SMS dedup, call dedup, leases)
- [ ] Add `UserState` model to `models.py`
- [ ] Add CDK resources for new tables

### Phase 2C: Daily Planning Lambda
- [ ] Implement `daily_plan_prompt.py` handler
  - Reset daily counters (`prompts_sent_today`, `daily_call_made`)
  - Calculate `next_prompt_at` from user timezone + preferred time
  - **Create/update "Kairos Debrief" event in user's Google Calendar**
    - Title: "📞 Kairos Debrief" (or configurable)
    - Time: `next_prompt_at` (15-minute duration)
    - Description: "Reply YES to the SMS to start your debrief call. Move this event to change the prompt time."
    - **Set `guestsCanModify: true`** so user can directly move/edit the event
    - Store `debrief_event_id` and `debrief_event_etag` in user state
  - **Create EventBridge Scheduler one-time schedule** for `next_prompt_at`
    - Target: Prompt Sender Lambda
    - Name: `kairos-prompt-{user_id}-{date}` (for idempotency)
    - Auto-delete after execution
  - Delete any stale schedules from previous days
  - Acquire daily lease to prevent duplicate runs
- [ ] Add EventBridge rule (8am UTC daily)
- [ ] Grant Lambda permission to create/delete EventBridge Scheduler schedules
- [ ] Add CDK resources

### Phase 2D: Prompt Sender Lambda
- [ ] Implement `prompt_sender.py` handler
  - **Invoked by EventBridge Scheduler at exact prompt time** (no polling)
  - **Read debrief event from Google Calendar** (user may have moved it)
    - If event deleted → skip today's prompt, delete the schedule
    - If event moved → reschedule (create new schedule at new time)
    - If event unchanged → proceed normally
  - Check idempotency (SMS not already sent today)
  - Get pending meetings from `kairos-meetings`
  - If no pending meetings → skip prompt, optionally delete calendar event
  - Send SMS via Twilio
  - Update user state (`prompts_sent_today`, `awaiting_reply`)
- [ ] Implement `twilio_sms.py` adapter (send SMS)
- [ ] Store Twilio credentials in SSM
- [ ] Add CDK resources (Lambda only - no recurring schedule needed)

### Phase 2E: SMS Webhook Handler
- [ ] Implement `sms_webhook.py` handler
  - Verify Twilio signature
  - Deduplicate by MessageSid
  - Parse intent (YES/READY/NO/STOP/UNKNOWN)
  - Update user state accordingly
  - Trigger call initiation for YES/READY
- [ ] Implement `twilio_sms.py` webhook verification
- [ ] Add Lambda Function URL for Twilio webhook
- [ ] Configure Twilio webhook URL
- [ ] Add CDK resources

### Phase 2F: Call Initiation Lambda
- [ ] Implement `initiate_daily_call.py` handler
  - Check call idempotency (`call-batch:{user_id}#{date}`)
  - Get all pending meetings
  - Build multi-meeting prompt
  - Call Bland AI
  - Record `daily_call_made = true`
- [ ] Add `build_multi_meeting_debrief_prompt()` to `prompts.py`
- [ ] Add CDK resources

### Phase 2G: Post-Call Processing
- [ ] Update `webhook.py` to:
  - Mark meetings as debriefed in DynamoDB
  - **Send summary via Twilio SMS** (instead of SES email)
  - **Delete or update the debrief calendar event** (mark as completed)
    - Option A: Delete the event (clean calendar)
    - Option B: Update title to "✅ Kairos Debrief (completed)"
- [ ] Add Twilio SSM permissions to webhook Lambda
- [ ] Update summarization prompt for SMS format (concise)

### Phase 2H: Testing & Polish
- [ ] Unit tests for intent parsing
- [ ] Unit tests for budget rules (can_prompt, can_call)
- [ ] Unit tests for idempotency (conditional write behavior)
- [ ] Integration test: multiple invocations don't double-send/double-call
- [ ] Add CloudWatch alarms for new Lambdas
- [ ] Manual end-to-end test

## Configuration (SSM Parameters)

```bash
# Google OAuth (existing from Phase 2A)
aws ssm put-parameter --name "/kairos/google-client-id" --value "xxx.apps.googleusercontent.com" --type String
aws ssm put-parameter --name "/kairos/google-client-secret" --value "GOCSPX-xxx" --type SecureString
aws ssm put-parameter --name "/kairos/google-refresh-token" --value "1//xxx" --type SecureString

# Twilio (new)
aws ssm put-parameter --name "/kairos/twilio-account-sid" --value "ACxxx" --type String
aws ssm put-parameter --name "/kairos/twilio-auth-token" --value "xxx" --type SecureString
aws ssm put-parameter --name "/kairos/twilio-phone-number" --value "+1xxx" --type String

# User settings (MVP: single user)
aws ssm put-parameter --name "/kairos/user-phone-number" --value "+44xxx" --type String
aws ssm put-parameter --name "/kairos/user-timezone" --value "Europe/London" --type String
```

## Cost Estimate (Per Day, ~10 meetings)

| Service | Cost |
|---------|------|
| Google Calendar API | Free (within quota) |
| EventBridge Scheduler | < $0.001 (2 schedules: 8am + prompt time) |
| Twilio SMS (send) | ~$0.02 (2 messages: prompt + summary) |
| Twilio SMS (receive) | ~$0.01 (1 reply) |
| Twilio Phone Number | ~$1/month |
| DynamoDB | < $0.01 |
| Lambda | < $0.01 (3-4 invocations, no polling) |
| Bland AI (1 call, 5 min) | ~$0.45 |
| **Total** | **~$0.48/day + $1/month Twilio** |

**Savings vs original Slice 2 design:**
- No Step Functions (~$0.025/day saved)
- No SES email (simpler stack, single notification channel)
- No polling (one-time schedules instead of every-5-min Lambda)
