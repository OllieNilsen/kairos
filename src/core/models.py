"""Pydantic models for all API contracts."""

from __future__ import annotations

from typing import Literal

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
    """Webhook payload from Bland AI on call completion."""

    call_id: str
    status: Literal["completed", "failed", "no-answer", "busy"]
    to: str
    from_number: str = Field(..., alias="from")
    started_at: str | None = None
    ended_at: str | None = None
    duration: int  # seconds
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    concatenated_transcript: str = ""
    variables: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}
