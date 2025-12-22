"""Bland AI voice client adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.core.models import TriggerPayload


class BlandClient:
    """Client for Bland AI voice API."""

    BASE_URL = "https://api.bland.ai/v1"
    TIMEOUT = 30.0

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
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
            "voice": "maya",  # Natural female voice
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

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None


# headers = {
#    'Authorization': 'API_KEY',
# }

# # Data
# data = {
#   "phone_number": "+447783597986",
#   "voice": "4f5222b2-230f-419b-b776-faa063392584",
#   "wait_for_greeting": false,
#   "record": true,
#   "answered_by_enabled": true,
#   "noise_cancellation": false,
#   "interruption_threshold": 500,
#   "block_interruptions": false,
#   "max_duration": 12,
#   "model": "base",
#   "language": "babel-en",
#   "background_track": "none",
#   "endpoint": "https://api.bland.ai",
#   "voicemail_action": "hangup",
#   "task": "You're Oystein Nilsen's new Artificial Chief of staff. You're tasked with calling Asimina, his partner, and finding out what she really thinks about Oystein. You'll be polite, brief and witty. You should stay away from topics that are too sensitive. You must not ask questions Asimina's already answered. You shouldn't repeat what Asimina has said, but you should acknowledge her responses.  The call should not last longer than 3 minutes.",
#   "first_sentence": "Hi Asimina! I'm Oystein's new Artificial chief of staff. How are you?"
# }

# # API request
# requests.post('https://api.bland.ai/v1/calls', json=data, headers=headers)
