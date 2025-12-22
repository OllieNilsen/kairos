"""Pydantic models for all API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
            parts.append(f"Attendees: {', '.join(self.attendees)}")
        parts.append(f"Duration: {self.duration_minutes()} minutes")
        return "\n".join(parts)
