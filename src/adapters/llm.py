"""Anthropic adapter for LLMClient interface."""

from __future__ import annotations

from typing import Any, TypeVar

import anthropic
from anthropic.types import TextBlock, ToolUseBlock
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class AnthropicAdapter:
    """Anthropic implementation of LLMClient."""

    # Use Haiku for speed/cost as per Slice 3 plan
    MODEL = "claude-3-haiku-20240307"
    MAX_TOKENS = 1024

    def __init__(self, api_key: str) -> None:
        self.client = anthropic.Anthropic(api_key=api_key)

    def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        """Get a simple text completion."""
        kwargs: dict[str, Any] = {
            "model": self.MODEL,
            "max_tokens": self.MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        message = self.client.messages.create(**kwargs)

        content = message.content[0]
        if isinstance(content, TextBlock):
            return content.text
        return ""

    def structured_completion(
        self, prompt: str, output_model: type[T], system_prompt: str | None = None
    ) -> T:
        """Get a structured output using Claude's tool use."""
        # Define the tool structure based on the Pydantic model
        tool_schema = output_model.model_json_schema()
        tool_name = "extract_entities"

        tool = {
            "name": tool_name,
            "description": "Extract structured data",
            "input_schema": tool_schema,
        }

        kwargs: dict[str, Any] = {
            "model": self.MODEL,
            "max_tokens": self.MAX_TOKENS,
            "system": system_prompt or "You are a helpful assistant. Extract data accurately.",
            "messages": [{"role": "user", "content": prompt}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool_name},  # Force tool use
        }

        message = self.client.messages.create(**kwargs)

        # Find the tool use block
        for block in message.content:
            if isinstance(block, ToolUseBlock) and block.name == tool_name:
                return output_model.model_validate(block.input)

        raise ValueError("LLM did not return the expected structured output tool call")
