"""Anthropic API client adapter for summarization."""

from __future__ import annotations

import anthropic
from anthropic.types import TextBlock


class AnthropicSummarizer:
    """Client for Anthropic Claude API."""

    MODEL = "claude-sonnet-4-20250514"
    MAX_TOKENS = 512

    def __init__(self, api_key: str) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)

    def summarize(self, transcript: str, system_prompt: str, user_prompt: str) -> str:
        """Summarize a transcript using Claude.

        Args:
            transcript: The conversation transcript (included in user_prompt)
            system_prompt: System instructions for summarization style
            user_prompt: The full user prompt with transcript and instructions

        Returns:
            The summary text
        """
        message = self.client.messages.create(
            model=self.MODEL,
            max_tokens=self.MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract text from the response
        content = message.content[0]
        if not isinstance(content, TextBlock):
            raise TypeError(f"Expected TextBlock, got {type(content).__name__}")
        return content.text
