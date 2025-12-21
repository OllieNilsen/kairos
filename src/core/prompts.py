"""Prompt templates for voice AI and summarization."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.models import EventContext


def build_debrief_system_prompt(context: EventContext, prompts: list[str]) -> str:
    """Build the system prompt for the Bland AI voice agent.

    Args:
        context: Event context (subject, participants, etc.)
        prompts: List of interview questions to ask

    Returns:
        System prompt string for the voice agent
    """
    participants_str = (
        ", ".join(context.participants) if context.participants else "the participants"
    )

    questions_block = "\n".join(f"- {p}" for p in prompts)

    return f"""You are Kairos, a professional AI assistant helping with a post-event debrief.

CONTEXT:
- Event Type: {context.event_type.replace("_", " ").title()}
- Subject: {context.subject}
- Participants: {participants_str}
{f"- Duration: {context.duration_minutes} minutes" if context.duration_minutes else ""}

YOUR TASK:
Conduct a brief, focused debrief interview. Ask the following questions naturally, one at a time:
{questions_block}

STYLE:
- Be conversational but efficient
- Acknowledge responses before moving to the next question
- If the user provides comprehensive answers, you may skip redundant questions
- Keep the call under 3 minutes
- End with "Thanks, I'll send you a summary shortly."

Do NOT:
- Ask questions not related to the debrief
- Engage in small talk beyond brief pleasantries
- Repeat information the user already provided
"""


def build_summarization_prompt(transcript: str, context: EventContext) -> str:
    """Build the prompt for summarizing the debrief transcript.

    Args:
        transcript: Full conversation transcript
        context: Original event context

    Returns:
        User message for the summarization request
    """
    return f"""Summarize this debrief call into an actionable SMS message (max 300 chars).

EVENT: {context.subject}
PARTICIPANTS: {", ".join(context.participants) if context.participants else "N/A"}

TRANSCRIPT:
{transcript}

FORMAT:
- Start with the event name
- List key decisions (if any)
- List action items with owners (if any)
- Note any risks/blockers (if any)
- Be extremely concise - this is an SMS

OUTPUT ONLY THE SUMMARY TEXT, nothing else."""
