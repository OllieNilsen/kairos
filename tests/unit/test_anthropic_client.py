"""Unit tests for Anthropic client adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import TextBlock

from src.adapters.anthropic_client import AnthropicSummarizer


class TestAnthropicSummarizer:
    """Tests for AnthropicSummarizer."""

    @pytest.fixture
    def mock_anthropic_client(self) -> MagicMock:
        """Create a mock Anthropic client."""
        return MagicMock()

    @pytest.fixture
    def summarizer(self, mock_anthropic_client: MagicMock) -> AnthropicSummarizer:
        """Create summarizer with mocked client."""
        with patch("anthropic.Anthropic") as mock_class:
            mock_class.return_value = mock_anthropic_client
            summ = AnthropicSummarizer(api_key="test-key")
            summ.client = mock_anthropic_client
            return summ

    def test_init(self) -> None:
        """Should initialize with API key."""
        with patch("anthropic.Anthropic") as mock_class:
            AnthropicSummarizer(api_key="test-api-key")
            mock_class.assert_called_once_with(api_key="test-api-key")

    def test_model_configuration(self) -> None:
        """Should have correct model configuration."""
        assert AnthropicSummarizer.MODEL == "claude-sonnet-4-20250514"
        assert AnthropicSummarizer.MAX_TOKENS == 512

    def test_summarize_success(
        self, summarizer: AnthropicSummarizer, mock_anthropic_client: MagicMock
    ) -> None:
        """Should summarize transcript successfully."""
        # Create a mock TextBlock response
        mock_text_block = MagicMock(spec=TextBlock)
        mock_text_block.text = "This is the summary of the meeting."

        mock_message = MagicMock()
        mock_message.content = [mock_text_block]
        mock_anthropic_client.messages.create.return_value = mock_message

        result = summarizer.summarize(
            transcript="User: Hello\nAgent: Hi there",
            system_prompt="You are a summarizer",
            user_prompt="Summarize this: User: Hello\nAgent: Hi there",
        )

        assert result == "This is the summary of the meeting."
        mock_anthropic_client.messages.create.assert_called_once_with(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system="You are a summarizer",
            messages=[{"role": "user", "content": "Summarize this: User: Hello\nAgent: Hi there"}],
        )

    def test_summarize_with_long_transcript(
        self, summarizer: AnthropicSummarizer, mock_anthropic_client: MagicMock
    ) -> None:
        """Should handle long transcripts."""
        mock_text_block = MagicMock(spec=TextBlock)
        mock_text_block.text = "Summary of long meeting"

        mock_message = MagicMock()
        mock_message.content = [mock_text_block]
        mock_anthropic_client.messages.create.return_value = mock_message

        long_transcript = "Line " * 1000  # Long transcript
        result = summarizer.summarize(
            transcript=long_transcript,
            system_prompt="Summarize",
            user_prompt=f"Summarize: {long_transcript}",
        )

        assert result == "Summary of long meeting"

    def test_summarize_raises_on_unexpected_content_type(
        self, summarizer: AnthropicSummarizer, mock_anthropic_client: MagicMock
    ) -> None:
        """Should raise TypeError if content is not TextBlock."""
        mock_content = MagicMock()  # Not a TextBlock
        # Make isinstance check fail by not being a TextBlock
        mock_content.__class__ = type("ImageBlock", (), {})

        mock_message = MagicMock()
        mock_message.content = [mock_content]
        mock_anthropic_client.messages.create.return_value = mock_message

        with pytest.raises(TypeError, match="Expected TextBlock"):
            summarizer.summarize(
                transcript="test",
                system_prompt="test",
                user_prompt="test",
            )
