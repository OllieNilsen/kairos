"""Bland AI voice client adapter."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from core.models import TriggerPayload

# Default voice - can be overridden via environment variable
# Rosalie: British female voice - "A young female British speaker"
# Voice ID from Bland AI voice library
DEFAULT_VOICE = os.environ.get(
    "BLAND_VOICE_ID", "a710fd26-0ed7-48e8-86b3-0d4e52d4f500"  # Rosalie - British Female
)


class BlandClient:
    """Client for Bland AI voice API."""

    BASE_URL = "https://api.bland.ai/v1"
    TIMEOUT = 30.0

    def __init__(self, api_key: str, voice: str | None = None) -> None:
        self.api_key = api_key
        self.voice = voice or DEFAULT_VOICE
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=self.TIMEOUT,
            )
        return self._client

    async def initiate_call(
        self,
        payload: TriggerPayload,
        system_prompt: str,
        webhook_url: str,
    ) -> str:
        """Initiate an outbound voice call.

        Args:
            payload: The trigger payload with phone and context
            system_prompt: The system prompt for the voice agent
            webhook_url: URL for Bland to call when the call ends

        Returns:
            The call_id from Bland AI

        Raises:
            httpx.HTTPStatusError: If the API call fails
        """
        client = await self._get_client()

        request_body = {
            "phone_number": payload.phone_number,
            "task": system_prompt,
            "voice": self.voice,
            "reduce_latency": True,
            "webhook": webhook_url,
            "metadata": {
                "event_context": payload.event_context.model_dump_json(),
            },
        }

        response = await client.post("/calls", json=request_body)
        response.raise_for_status()

        data: dict[str, str] = response.json()
        return data["call_id"]

    async def initiate_call_raw(
        self,
        phone_number: str,
        system_prompt: str,
        webhook_url: str,
        variables: dict[str, object] | None = None,
    ) -> str:
        """Initiate an outbound voice call with raw parameters.

        Args:
            phone_number: E.164 phone number to call
            system_prompt: The system prompt for the voice agent
            webhook_url: URL for Bland to call when the call ends
            variables: Optional variables to pass through to webhook

        Returns:
            The call_id from Bland AI

        Raises:
            httpx.HTTPStatusError: If the API call fails
        """
        client = await self._get_client()

        request_body: dict[str, object] = {
            "phone_number": phone_number,
            "task": system_prompt,
            "voice": self.voice,
            "reduce_latency": True,
            "webhook": webhook_url,
        }

        if variables:
            request_body["metadata"] = variables

        response = await client.post("/calls", json=request_body)
        response.raise_for_status()

        data: dict[str, str] = response.json()
        return data["call_id"]

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
