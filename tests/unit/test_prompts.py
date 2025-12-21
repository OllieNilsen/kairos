"""Unit tests for prompt builders."""

from src.core.models import EventContext
from src.core.prompts import build_debrief_system_prompt, build_summarization_prompt


class TestDebriefSystemPrompt:
    """Tests for build_debrief_system_prompt."""

    def test_includes_context(self):
        """Prompt should include event context details."""
        context = EventContext(
            event_type="meeting_debrief",
            subject="Q4 Planning Session",
            participants=["Sarah Chen", "Mike Ross"],
            duration_minutes=45,
        )
        prompts = ["What were the key decisions?"]

        result = build_debrief_system_prompt(context, prompts)

        assert "Q4 Planning Session" in result
        assert "Sarah Chen" in result
        assert "Mike Ross" in result
        assert "45 minutes" in result
        assert "What were the key decisions?" in result

    def test_handles_empty_participants(self):
        """Should handle empty participants list gracefully."""
        context = EventContext(
            event_type="call_debrief",
            subject="Sales Call",
            participants=[],
        )

        result = build_debrief_system_prompt(context, ["How did it go?"])

        assert "the participants" in result


class TestSummarizationPrompt:
    """Tests for build_summarization_prompt."""

    def test_includes_transcript(self):
        """Prompt should include the transcript."""
        context = EventContext(
            event_type="meeting_debrief",
            subject="Team Standup",
            participants=["Alice", "Bob"],
        )
        transcript = "User: The meeting went great. We decided to ship next week."

        result = build_summarization_prompt(transcript, context)

        assert transcript in result
        assert "Team Standup" in result
        assert "Alice" in result
        assert "SMS" in result  # Format instruction
