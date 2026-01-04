"""Core interfaces for infrastructure dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    # Support both Lambda and test import paths
    try:
        from core.models import (
            CandidateScore,
            Entity,
            EntityType,
            Mention,
            TranscriptSegment,
        )
    except ImportError:
        from src.core.models import (
            CandidateScore,
            Entity,
            EntityType,
            Mention,
            TranscriptSegment,
        )

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


class EntitiesRepositoryProtocol(Protocol):
    """Interface for entity storage."""

    def get_by_id(self, user_id: str, entity_id: str) -> Entity | None: ...
    def query_by_alias(self, user_id: str, alias_query: str) -> list[str]: ...
    def create_provisional(
        self, user_id: str, mention_text: str, entity_type: EntityType
    ) -> Entity: ...
    def get_or_create_by_email(self, user_id: str, email: str, name: str) -> Entity: ...


class MentionsRepositoryProtocol(Protocol):
    """Interface for mention storage."""

    def create_mention(self, mention: Mention) -> None: ...
    def mark_linked(
        self, user_id: str, mention_id: str, entity_id: str, confidence: float
    ) -> None: ...
    def mark_ambiguous(
        self, user_id: str, mention_id: str, candidates: list[str], scores: list[CandidateScore]
    ) -> None: ...


class TranscriptsRepositoryProtocol(Protocol):
    """Interface for transcript storage."""

    def get_transcript(self, user_id: str, meeting_id: str) -> list[TranscriptSegment]: ...
