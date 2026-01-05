"""Pydantic models for all API contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

# === Slice 3: Knowledge Graph Enums ===


class EntityType(str, Enum):
    """Types of entities in the knowledge graph."""

    PERSON = "Person"
    ORGANIZATION = "Organization"
    PROJECT = "Project"


class EntityStatus(str, Enum):
    """Status of an entity in the knowledge graph."""

    RESOLVED = "resolved"  # Has strong identifier (email) or user-confirmed
    PROVISIONAL = "provisional"  # Created from mentions, awaiting confirmation
    MERGED = "merged"  # Entity was merged into another (tombstone)


class ResolutionState(str, Enum):
    """Resolution state of a mention."""

    LINKED = "linked"  # Successfully matched to existing entity
    AMBIGUOUS = "ambiguous"  # Multiple candidates, awaiting resolution
    NEW_ENTITY_CREATED = "new_entity_created"  # No match, created new provisional entity


class EdgeType(str, Enum):
    """Types of relationships between entities."""

    WORKS_AT = "WORKS_AT"  # Person -> Organization
    WORKS_ON = "WORKS_ON"  # Person -> Project
    RELATES_TO = "RELATES_TO"  # Person -> Person (with label)
    INTRODUCED = "INTRODUCED"  # Person -> Person (who introduced whom)


# === User State (Slice 2 MVP) ===


class UserState(BaseModel):
    """User state for notification budget and scheduling.

    Stored in kairos-user-state DynamoDB table.
    """

    user_id: str

    # Contact info
    phone_number: str | None = None  # E.164 format
    email: str | None = None  # Optional, for future use

    # Timezone and scheduling
    timezone: str = "Europe/London"
    preferred_prompt_time: str = "17:30"  # HH:MM format
    next_prompt_at: str | None = None  # ISO8601 - when to send today's prompt
    prompt_schedule_name: str | None = None  # EventBridge Scheduler schedule name
    debrief_event_id: str | None = None  # Google Calendar event ID for today's debrief
    debrief_event_etag: str | None = None  # For detecting user modifications

    # Daily state (reset each morning by daily_plan_prompt)
    prompts_sent_today: int = 0
    last_prompt_at: str | None = None  # ISO8601
    awaiting_reply: bool = False
    active_prompt_id: str | None = None
    daily_call_made: bool = False
    call_successful: bool = False  # True only if call completed successfully
    retries_today: int = 0  # Count of retry attempts today (max 3)
    last_call_at: str | None = None  # ISO8601
    next_retry_at: str | None = None  # ISO8601 - scheduled retry time
    retry_schedule_name: str | None = None  # EventBridge schedule name for retry
    daily_batch_id: str | None = None
    last_daily_reset: str | None = None  # ISO8601 - when counters were last reset

    # Control state
    snooze_until: str | None = None  # ISO8601 - don't prompt/call until this time
    stopped: bool = False  # User opted out (STOP) - never prompt/call

    # Google Calendar push subscription (refresh token is in SSM, not here)
    google_channel_id: str | None = None
    google_channel_expiry: str | None = None


# === SMS Intent (Slice 2 - Twilio Integration) ===


class SMSIntent(str, Enum):
    """Parsed intent from inbound SMS message.

    Used to determine user's response to the daily debrief prompt.
    """

    YES = "yes"  # User wants to start the call now
    READY = "ready"  # User is ready (also clears snooze)
    NO = "no"  # User wants to skip/snooze until tomorrow
    STOP = "stop"  # User wants to opt out of all future prompts
    UNKNOWN = "unknown"  # Could not parse intent - send help message


class TwilioInboundSMS(BaseModel):
    """Inbound SMS webhook payload from Twilio.

    Twilio sends form-encoded data; we parse it into this model.
    See: https://www.twilio.com/docs/messaging/guides/webhook-request
    """

    # Required fields from Twilio
    MessageSid: str  # Unique identifier for this message
    AccountSid: str  # Twilio account SID
    From: str  # Sender phone number (E.164 format)
    To: str  # Recipient phone number (our Twilio number)
    Body: str  # Message text content

    # Optional fields
    NumMedia: int = 0  # Number of media attachments
    NumSegments: int = 1  # Number of SMS segments

    # Geographic info (may not always be present)
    FromCity: str | None = None
    FromState: str | None = None
    FromZip: str | None = None
    FromCountry: str | None = None
    ToCity: str | None = None
    ToState: str | None = None
    ToZip: str | None = None
    ToCountry: str | None = None

    model_config = {"extra": "ignore"}  # Ignore additional Twilio fields


# === Trigger Payload (User -> Trigger Lambda) ===


class EventContext(BaseModel):
    """Context about the event being debriefed."""

    event_type: Literal["meeting_debrief", "call_debrief", "general"]
    subject: str = Field(..., min_length=1, max_length=200)
    participants: list[str] = Field(default_factory=list)
    duration_minutes: int | None = None


class TriggerPayload(BaseModel):
    """Incoming request to initiate a debrief call."""

    phone_number: str = Field(..., pattern=r"^\+[1-9]\d{6,14}$")  # E.164 international
    event_context: EventContext
    interview_prompts: list[str] = Field(..., min_length=1, max_length=5)


class TriggerResponse(BaseModel):
    """Response from the trigger endpoint."""

    status: Literal["initiated", "error"]
    call_id: str | None = None
    message: str


# === Bland AI Webhook Payload ===


class TranscriptTurn(BaseModel):
    """A single turn in the conversation transcript from Bland AI.

    Bland sends transcripts with timestamps and unique IDs.
    The 'user' field indicates the speaker (we alias it to 'speaker' for clarity).
    """

    id: int  # Unique segment ID from Bland
    user: Literal[
        "assistant", "user", "agent-action"
    ]  # Bland uses 'user' for speaker, 'agent-action' for actions
    text: str
    created_at: str  # ISO timestamp when this segment was spoken

    @property
    def speaker(self) -> Literal["assistant", "user", "agent-action"]:
        """Alias for 'user' field for backward compatibility."""
        return self.user


class BlandWebhookPayload(BaseModel):
    """Webhook payload from Bland AI on call completion.

    Note: Bland's actual payload varies. We accept extra fields and make most optional.
    """

    call_id: str
    status: str  # Bland sends various statuses, don't restrict
    to: str = ""
    from_number: str = Field(default="", alias="from")
    started_at: str | None = Field(default=None, alias="started_at")
    end_at: str | None = None  # Bland uses 'end_at' not 'ended_at'
    call_length: float | None = None  # Bland uses call_length (in minutes)
    transcripts: list[TranscriptTurn] = Field(default_factory=list)  # Bland uses 'transcripts'
    concatenated_transcript: str = ""
    variables: dict[str, Any] = Field(default_factory=dict)  # Can be nested
    answered_by: str | None = None  # e.g., "voicemail", "human"
    corrected_duration: str | None = None  # Duration in seconds as string

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @property
    def transcript(self) -> list[TranscriptTurn]:
        """Alias for backward compatibility."""
        return self.transcripts


# === Meeting Models (Slice 2) ===


class AttendeeInfo(BaseModel):
    """Attendee information from calendar event.

    Used for deterministic entity resolution via email.
    """

    name: str
    email: str | None = None  # None if email not available


class Meeting(BaseModel):
    """A calendar meeting synced from Google Calendar.

    Note: attendees field supports both old format (list[str] of emails) and
    new format (list[AttendeeInfo] with name + email). A validator normalizes
    old format to new format on load.
    """

    user_id: str
    meeting_id: str  # Google Calendar event ID
    title: str
    description: str | None = None  # Meeting agenda/notes from calendar
    location: str | None = None  # Meeting location or video link
    start_time: datetime
    end_time: datetime
    attendees: list[AttendeeInfo] = Field(default_factory=list)
    attendee_entity_ids: list[str] = Field(default_factory=list)  # Linked entity IDs (Slice 3)
    status: Literal["pending", "debriefed", "skipped"] = "pending"
    google_etag: str | None = None  # For sync conflict detection
    created_at: datetime = Field(default_factory=datetime.now)

    @field_validator("attendees", mode="before")
    @classmethod
    def normalize_attendees(cls, v: Any) -> list[dict[str, Any]]:
        """Convert old format (list of emails) to new format (AttendeeInfo)."""
        if not v:
            return []
        result = []
        for item in v:
            if isinstance(item, str):
                # Old format: just an email string - use email as name too
                result.append({"name": item, "email": item})
            elif isinstance(item, dict):
                # Dict from DynamoDB/JSON - pass through
                result.append(item)
            elif hasattr(item, "model_dump"):
                # Already an AttendeeInfo
                result.append(item.model_dump())
            else:
                continue
        return result

    @property
    def attendee_emails(self) -> list[str]:
        """Get list of attendee emails for backward compatibility."""
        return [a.email for a in self.attendees if a.email]

    @property
    def attendee_names(self) -> list[str]:
        """Get list of attendee display names."""
        return [a.name for a in self.attendees]

    def duration_minutes(self) -> int:
        """Calculate meeting duration in minutes."""
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)

    def to_event_context(self) -> EventContext:
        """Convert to EventContext for debrief call."""
        return EventContext(
            event_type="meeting_debrief",
            subject=self.title,
            participants=self.attendee_names,  # Use names for context
            duration_minutes=self.duration_minutes(),
        )

    def context_for_prompt(self) -> str:
        """Generate rich context string for AI prompts."""
        parts = [f"Meeting: {self.title}"]
        if self.description:
            parts.append(f"Agenda/Notes: {self.description}")
        if self.location:
            parts.append(f"Location: {self.location}")
        if self.attendees:
            parts.append(f"Attendees: {', '.join(self.attendee_names)}")
        parts.append(f"Duration: {self.duration_minutes()} minutes")
        return "\n".join(parts)


# === Slice 4: Kairos Calendar Normal Form (KCNF) ===


class OrganizerInfo(BaseModel):
    """Organizer information for a calendar event."""

    name: str | None = None
    email: str | None = None


class ConferenceInfo(BaseModel):
    """Conference/video call information for a calendar event."""

    join_url: str | None = None
    conference_id: str | None = None
    phone: str | None = None


class RecurrenceInfo(BaseModel):
    """Recurrence metadata for recurring calendar events.

    Required for MVP to handle series, instances, and exceptions correctly.
    """

    provider_series_id: str | None = None  # Series master ID (Google: recurringEventId)
    provider_instance_id: str | None = None  # Instance ID if this is an occurrence
    is_recurring_instance: bool = False  # True if this is an instance of a series
    is_exception: bool = False  # True if modified from series pattern
    original_start: datetime | None = None  # Original start time for exceptions (before moved)
    recurrence_rule: str | None = None  # RRULE (for series masters only)


class KairosCalendarEvent(BaseModel):
    """Provider-agnostic calendar event model (KCNF).

    Normalizes Google Calendar and Microsoft Graph events into a single format.
    All downstream logic (briefings, invite triage, debrief selection) uses KCNF.
    """

    # === Tenant + provider identity ===
    user_id: str
    provider: Literal["google", "microsoft"]
    provider_calendar_id: str | None = None
    provider_event_id: str
    provider_etag: str | None = None  # Google (preserved for reference/debug only)
    provider_change_key: str | None = None  # Microsoft (preserved for reference/debug only)
    provider_version: str  # Unified version guard (MUST be set on ingest):
    # - Google: provider_version = provider_etag
    # - Microsoft: provider_version = provider_change_key
    # - Fallback: last_modified_at ISO string (only if provider lacks version token)
    #
    # NOTE: provider_etag and provider_change_key are preserved for reference/debug,
    # but MUST NOT be used directly for concurrency guards or staleness checks.
    # Always use provider_version.

    # === Core event fields ===
    title: str | None = None
    description: str | None = None
    location: str | None = None

    start: datetime  # MUST be tz-aware for GSI_DAY computation
    end: datetime  # MUST be tz-aware
    is_all_day: bool = False
    status: str | None = None  # confirmed/cancelled/tentative

    # === People ===
    organizer: OrganizerInfo | None = None
    attendees: list[AttendeeInfo] = Field(default_factory=list)

    # === Conferencing ===
    conference: ConferenceInfo | None = None

    # === Recurrence (required for MVP) ===
    recurrence: RecurrenceInfo | None = None

    # === Kairos metadata ===
    is_debrief_event: bool = False  # True if this is a Kairos-created debrief event
    kairos_tags: dict[str, Any] = Field(default_factory=dict)  # Normalized provider extensions

    # === Sync/audit ===
    ingested_at: datetime
    last_modified_at: datetime | None = None  # Provider last modified timestamp (for reference)

    # === DynamoDB-specific fields (not part of logical model) ===
    item_type: Literal["event", "redirect"] = "event"  # For tombstone redirects
    redirect_to_sk: str | None = None  # Target SK if item_type="redirect"
    ttl: int | None = None  # Unix timestamp for DynamoDB TTL (180 days from end)


# === Slice 3: Knowledge Graph Models ===


class TranscriptSegment(BaseModel):
    """A segment of a meeting transcript.

    Transcripts are stored as segments to enable reliable verification
    of extracted quotes against specific portions of the transcript.
    """

    segment_id: str
    t0: float  # Start time in seconds
    t1: float  # End time in seconds
    speaker: str | None = None  # Diarization label if available
    text: str  # Raw transcript text for this segment


class MentionEvidence(BaseModel):
    """Evidence for a mention extraction.

    Contains the grounding information that ties a mention to the transcript.
    """

    meeting_id: str
    segment_id: str
    t0: float  # Start time in seconds
    t1: float  # End time in seconds
    quote: str  # Exact text from transcript (verified)


class Entity(BaseModel):
    """An entity in the knowledge graph.

    Represents a Person, Organization, or Project that persists across meetings.
    """

    entity_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    type: EntityType
    display_name: str
    canonical_name: str | None = None  # User-confirmed name
    primary_email: str | None = None  # Deterministic identifier for Person
    aliases: list[str] = Field(default_factory=list)
    status: EntityStatus = EntityStatus.PROVISIONAL

    # Merge tracking (for tombstones)
    merged_into: str | None = None  # Target entity_id if status=MERGED
    merged_at: str | None = None  # ISO8601 when merge occurred

    # Cached/derived fields for scoring
    organization: str | None = None  # Derived from WORKS_AT edge
    role: str | None = None  # Most recent role_hint from mentions
    recent_meeting_ids: list[str] = Field(default_factory=list)  # Max 10

    # Embedding for semantic search (Phase 3G)
    profile_embedding_id: str | None = None

    # Evidence and stats
    top_evidence: list[MentionEvidence] = Field(default_factory=list)  # Max 10
    mention_count: int = 0
    edge_count: int = 0
    last_seen: str | None = None  # ISO8601
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class Mention(BaseModel):
    """An extracted mention from a transcript.

    Represents a single occurrence of an entity being mentioned in a meeting.
    """

    mention_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    mention_text: str  # Exact text as spoken
    type: EntityType
    local_context: str  # 1-2 surrounding sentences

    # Evidence
    evidence: MentionEvidence

    # Hints extracted from context
    role_hint: str | None = None  # "CFO", "recruiter", etc.
    org_hint: str | None = None  # Organization mentioned in same context
    speaker_email: str | None = None  # From diarization, if mapped

    # Meeting context for scoring
    meeting_attendee_emails: list[str] = Field(default_factory=list)

    # Resolution state
    resolution_state: ResolutionState = ResolutionState.AMBIGUOUS
    linked_entity_id: str | None = None  # Final linked entity
    candidate_entity_ids: list[str] = Field(default_factory=list)  # For ambiguous
    candidate_scores: list[dict[str, Any]] = Field(
        default_factory=list
    )  # {entity_id, score, reasoning}
    confidence: float = 0.0

    # Metadata
    extractor_version: str = "1.0"
    verified: bool = False  # Passed deterministic validation
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class EdgeEvidence(BaseModel):
    """Evidence for a relationship edge."""

    meeting_id: str
    quote: str
    t0: float
    t1: float


class Edge(BaseModel):
    """A relationship edge between entities.

    Stored with dual-write pattern (EDGEOUT and EDGEIN) for bidirectional queries.
    """

    user_id: str
    from_entity_id: str
    to_entity_id: str
    edge_type: EdgeType
    meeting_id: str  # Where this relationship was established

    # Properties (type-specific)
    properties: dict[str, Any] = Field(default_factory=dict)
    # For RELATES_TO: {"label": "advisor", "cofounder", "investor"}
    # For INTRODUCED: {"introduced_by": entity_id}

    # Evidence (max 5)
    evidence: list[EdgeEvidence] = Field(default_factory=list)
    confidence: float = 0.0
    verified: bool = False  # Passed LLM entailment check

    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# === Slice 3: Extraction & Verification Models ===


class MentionExtraction(BaseModel):
    """Raw extraction output from LLM before verification.

    This is the LLM's output; it must be verified before becoming a Mention.
    """

    mention_text: str
    type: EntityType
    segment_id: str
    quote: str
    t0: float | None = None
    t1: float | None = None
    role_hint: str | None = None
    org_hint: str | None = None


class VerificationResult(BaseModel):
    """Result of verifying an extraction against the transcript.

    Contains the cleaned extraction with unverified optional fields stripped.
    """

    is_valid: bool
    cleaned_extraction: MentionExtraction | None = None  # None if rejected
    errors: list[str] = Field(default_factory=list)  # Blocking errors
    warnings: list[str] = Field(default_factory=list)  # Non-blocking (fields stripped)


class EntailmentResult(BaseModel):
    """Result of LLM entailment check for relationships.

    Used to verify that a quote actually supports a claimed relationship.
    """

    verdict: Literal["SUPPORTED", "NOT_SUPPORTED", "AMBIGUOUS"]
    rationale: str


# === Slice 3: Resolution Models ===


class CandidateQuery(BaseModel):
    """Rich query object for candidate retrieval.

    Contains all context needed to find and score candidate entities.
    """

    mention_text: str
    meeting_id: str
    meeting_attendees: list[AttendeeInfo] = Field(default_factory=list)
    local_context: str = ""
    role_hint: str | None = None
    speaker_email: str | None = None  # From diarization
    mention_embedding: list[float] | None = None  # Phase 3G


class CandidateScore(BaseModel):
    """Score for a candidate entity match.

    Contains LLM-generated score with reasoning for transparency.
    """

    entity_id: str
    score: float  # 0.0 - 1.0
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasoning: str


# === Slice 3: Helper Functions ===
# Re-exported from transcript_utils for backward compatibility

try:
    from core.transcript_utils import convert_bland_transcript, normalize_text
except ImportError:
    from src.core.transcript_utils import convert_bland_transcript, normalize_text

__all__ = [
    # ... existing exports ...
    "normalize_text",
    "convert_bland_transcript",
]
