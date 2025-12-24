"""Pydantic models for all API contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


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
    """A single turn in the conversation transcript."""

    speaker: Literal["assistant", "user"]
    text: str


class BlandWebhookPayload(BaseModel):
    """Webhook payload from Bland AI on call completion.

    Note: Bland's actual payload varies. We accept extra fields and make most optional.
    """

    call_id: str
    status: str  # Bland sends various statuses, don't restrict
    to: str = ""
    from_number: str = Field(default="", alias="from")
    started_at: str | None = None
    ended_at: str | None = None
    call_length: float | None = None  # Bland uses call_length (in minutes)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    concatenated_transcript: str = ""
    variables: dict[str, Any] = Field(default_factory=dict)  # Can be nested

    model_config = {"populate_by_name": True, "extra": "ignore"}


# === Meeting Models (Slice 2) ===


class Meeting(BaseModel):
    """A calendar meeting synced from Google Calendar."""

    user_id: str
    meeting_id: str  # Google Calendar event ID
    title: str
    description: str | None = None  # Meeting agenda/notes from calendar
    location: str | None = None  # Meeting location or video link
    start_time: datetime
    end_time: datetime
    attendees: list[str] = Field(default_factory=list)
    status: Literal["pending", "debriefed", "skipped"] = "pending"
    google_etag: str | None = None  # For sync conflict detection
    created_at: datetime = Field(default_factory=datetime.now)

    def duration_minutes(self) -> int:
        """Calculate meeting duration in minutes."""
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)

    def to_event_context(self) -> EventContext:
        """Convert to EventContext for debrief call."""
        return EventContext(
            event_type="meeting_debrief",
            subject=self.title,
            participants=self.attendees,
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
            # Handle both old format (list[str]) and new format (list[AttendeeInfo])
            attendee_names = []
            for a in self.attendees:
                if isinstance(a, str):
                    attendee_names.append(a)
                elif hasattr(a, "name"):
                    attendee_names.append(a.name)
            parts.append(f"Attendees: {', '.join(attendee_names)}")
        parts.append(f"Duration: {self.duration_minutes()} minutes")
        return "\n".join(parts)


# === Slice 3: Knowledge Graph Models ===


class AttendeeInfo(BaseModel):
    """Attendee information from calendar event.

    Used for deterministic entity resolution via email.
    """

    name: str
    email: str | None = None  # None if email not available


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
    candidate_scores: list[dict] = Field(default_factory=list)  # {entity_id, score, reasoning}
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
    properties: dict = Field(default_factory=dict)
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
