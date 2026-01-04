"""Unit tests for transcript helper functions."""

from __future__ import annotations

from src.core.models import (
    TranscriptTurn,
    convert_bland_transcript,
    normalize_text,
)


class TestNormalizeText:
    """Tests for normalize_text function."""

    def test_lowercase_conversion(self) -> None:
        """Should convert text to lowercase."""
        assert normalize_text("Hello World") == "hello world"
        assert normalize_text("UPPERCASE") == "uppercase"

    def test_punctuation_removal(self) -> None:
        """Should remove punctuation except apostrophes."""
        assert normalize_text("Hello, world!") == "hello world"
        assert normalize_text("What's up?") == "what's up"
        assert normalize_text("Don't stop.") == "don't stop"

    def test_whitespace_collapse(self) -> None:
        """Should collapse multiple spaces into one."""
        assert normalize_text("Hello    world") == "hello world"
        assert normalize_text("  Leading and trailing  ") == "leading and trailing"

    def test_diarization_tag_removal(self) -> None:
        """Should remove diarization tags."""
        assert normalize_text("[Speaker 1]: Hello") == "hello"
        assert normalize_text("[speaker]: World") == "world"
        assert normalize_text("[Speaker]: Test") == "test"

    def test_complex_text(self) -> None:
        """Should handle complex text with multiple transformations."""
        text = "[Speaker 1]: Hello, I'm John! What's your name?"
        expected = "hello i'm john what's your name"
        assert normalize_text(text) == expected

    def test_empty_string(self) -> None:
        """Should handle empty string."""
        assert normalize_text("") == ""

    def test_only_punctuation(self) -> None:
        """Should handle text with only punctuation."""
        assert normalize_text("!!!") == ""
        assert normalize_text("...") == ""


class TestConvertBlandTranscript:
    """Tests for convert_bland_transcript function."""

    def test_basic_conversion(self) -> None:
        """Should convert Bland transcript turns to segments."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="Hello, how are you?",
                created_at="2024-01-15T10:00:05Z",
            ),
            TranscriptTurn(
                id=2,
                user="assistant",
                text="I'm doing well, thank you!",
                created_at="2024-01-15T10:00:10Z",
            ),
        ]

        segments = convert_bland_transcript(turns, call_start_time="2024-01-15T10:00:00Z")

        assert len(segments) == 2
        assert segments[0].segment_id == "seg_0001"
        assert segments[0].speaker == "user"
        assert segments[0].text == "Hello, how are you?"
        assert segments[1].segment_id == "seg_0002"
        assert segments[1].speaker == "assistant"

    def test_timestamp_calculation(self) -> None:
        """Should calculate relative timestamps from call start."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="First message.",
                created_at="2024-01-15T10:00:05Z",
            ),
            TranscriptTurn(
                id=2,
                user="assistant",
                text="Second message.",
                created_at="2024-01-15T10:00:15Z",
            ),
        ]

        segments = convert_bland_transcript(turns, call_start_time="2024-01-15T10:00:00Z")

        # First segment starts at 5 seconds
        assert segments[0].t0 == 5.0
        # Second segment starts at 15 seconds
        assert segments[1].t0 == 15.0

    def test_duration_estimation(self) -> None:
        """Should use next segment's start time for t1, or estimate for last segment."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="Short.",  # 1 word
                created_at="2024-01-15T10:00:00Z",
            ),
            TranscriptTurn(
                id=2,
                user="user",
                text="This is a longer message with more words.",  # 8 words
                created_at="2024-01-15T10:00:05Z",
            ),
        ]

        segments = convert_bland_transcript(turns, call_start_time="2024-01-15T10:00:00Z")

        # First segment's t1 should be next segment's start time (5.0)
        assert segments[0].t0 == 0.0
        assert segments[0].t1 == 5.0  # Uses actual next segment timestamp

        # Last segment's t1 should be estimated from word count
        assert segments[1].t0 == 5.0
        assert segments[1].t1 > segments[1].t0  # Estimated duration

    def test_without_call_start_time(self) -> None:
        """Should handle missing call_start_time gracefully."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="Test message.",
                created_at="2024-01-15T10:00:05Z",
            ),
        ]

        segments = convert_bland_transcript(turns, call_start_time=None)

        assert len(segments) == 1
        assert segments[0].segment_id == "seg_0001"
        assert segments[0].t0 == 0.0
        assert segments[0].t1 == 0.0

    def test_invalid_call_start_time(self) -> None:
        """Should handle invalid call_start_time gracefully."""
        turns = [
            TranscriptTurn(
                id=1,
                user="user",
                text="Test message.",
                created_at="2024-01-15T10:00:05Z",
            ),
        ]

        segments = convert_bland_transcript(turns, call_start_time="invalid")

        assert len(segments) == 1
        assert segments[0].t0 == 0.0
        assert segments[0].t1 == 0.0

    def test_segment_id_formatting(self) -> None:
        """Should format segment IDs with zero padding."""
        turns = [
            TranscriptTurn(id=1, user="user", text="First", created_at=""),
            TranscriptTurn(id=10, user="user", text="Tenth", created_at=""),
            TranscriptTurn(id=100, user="user", text="Hundredth", created_at=""),
        ]

        segments = convert_bland_transcript(turns)

        assert segments[0].segment_id == "seg_0001"
        assert segments[1].segment_id == "seg_0010"
        assert segments[2].segment_id == "seg_0100"

    def test_empty_transcript(self) -> None:
        """Should handle empty transcript list."""
        segments = convert_bland_transcript([])

        assert segments == []

    def test_preserves_speaker_role(self) -> None:
        """Should preserve speaker role from Bland transcript."""
        turns = [
            TranscriptTurn(id=1, user="user", text="User message", created_at=""),
            TranscriptTurn(id=2, user="assistant", text="Assistant message", created_at=""),
        ]

        segments = convert_bland_transcript(turns)

        assert segments[0].speaker == "user"
        assert segments[1].speaker == "assistant"

    def test_missing_created_at(self) -> None:
        """Should handle missing created_at timestamps."""
        turns = [
            TranscriptTurn(id=1, user="user", text="Test", created_at=""),
        ]

        segments = convert_bland_transcript(turns, call_start_time="2024-01-15T10:00:00Z")

        assert len(segments) == 1
        # Should default to 0.0 when created_at is empty
        assert segments[0].t0 == 0.0
        assert segments[0].t1 == 0.0
