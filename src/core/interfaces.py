"""Core interfaces for infrastructure dependencies."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    """Interface for LLM interactions."""

    def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        """Get a simple text completion."""
        ...

    def structured_completion(
        self, prompt: str, output_model: type[T], system_prompt: str | None = None
    ) -> T:
        """Get a structured output validated against a Pydantic model."""
        ...
