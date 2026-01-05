"""Microsoft Graph API adapter."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

# Microsoft OAuth2 and Graph API endpoints
TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4, 8]  # Exponential backoff in seconds


class MicrosoftGraphClient:
    """Client for Microsoft Graph API using OAuth2."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        refresh_token: str,
    ) -> None:
        """Initialize client with OAuth credentials.

        Args:
            client_id: Azure AD application (client) ID
            client_secret: Azure AD application secret
            tenant_id: Azure AD tenant ID (or "common" for multi-tenant)
            refresh_token: OAuth refresh token for user
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.refresh_token = refresh_token
        self._access_token: str | None = None
        self._token_expiry: datetime | None = None

    @classmethod
    def from_ssm(cls, user_id: str) -> MicrosoftGraphClient:
        """Create client using credentials from SSM Parameter Store.

        Args:
            user_id: User ID for per-user refresh token lookup

        Returns:
            Configured MicrosoftGraphClient instance
        """
        # Import here to avoid module-level dependency on SSM (for testing)
        from src.adapters.ssm import get_parameter

        return cls(
            client_id=get_parameter("/kairos/microsoft/client-id", decrypt=False),
            client_secret=get_parameter("/kairos/microsoft/client-secret"),
            tenant_id=get_parameter("/kairos/microsoft/tenant-id", decrypt=False, default="common"),
            refresh_token=get_parameter(f"/kairos/users/{user_id}/microsoft/refresh-token"),
        )

    def _get_access_token(self) -> str:
        """Get a valid access token, refreshing if necessary.

        Returns:
            Valid access token string

        Raises:
            httpx.HTTPStatusError: If token refresh fails
        """
        now = datetime.now(UTC)

        # Return cached token if still valid (with 5 min buffer)
        if (
            self._access_token
            and self._token_expiry
            and now < self._token_expiry - timedelta(minutes=5)
        ):
            return self._access_token

        # Refresh the access token
        token_url = TOKEN_URL.format(tenant_id=self.tenant_id)
        response = httpx.post(
            token_url,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        self._access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expiry = now + timedelta(seconds=expires_in)

        return self._access_token

    def _request(
        self,
        method: str,
        endpoint: str,
        retry: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an authenticated request to the Graph API with retry logic.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            endpoint: API endpoint (relative to GRAPH_API_BASE or full URL)
            retry: Whether to retry on transient failures
            **kwargs: Additional arguments for httpx.request

        Returns:
            httpx.Response object

        Raises:
            httpx.HTTPStatusError: On API errors after retries
        """
        # Build full URL
        url = endpoint if endpoint.startswith("http") else f"{GRAPH_API_BASE}{endpoint}"

        # Prepare headers
        headers = kwargs.pop("headers", {})

        # Try request with retries
        last_exception = None
        for attempt in range(MAX_RETRIES + 1 if retry else 1):
            try:
                # Get fresh token (handles token refresh automatically)
                token = self._get_access_token()
                headers["Authorization"] = f"Bearer {token}"

                response = httpx.request(
                    method,
                    url,
                    headers=headers,
                    timeout=30.0,
                    **kwargs,
                )
                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code

                # Don't retry on client errors (except 401, 429)
                if status_code == 401:
                    # Token expired, clear cache and retry
                    self._access_token = None
                    if attempt < MAX_RETRIES:
                        continue
                elif status_code == 429:
                    # Rate limited, respect Retry-After header
                    retry_after = e.response.headers.get("Retry-After", "2")
                    try:
                        delay = int(retry_after)
                    except ValueError:
                        delay = 2
                    if attempt < MAX_RETRIES:
                        time.sleep(delay)
                        continue
                elif status_code >= 500:
                    # Server error, retry with exponential backoff
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                        time.sleep(delay)
                        continue

                # For all other errors or max retries exceeded, raise
                raise

            except (httpx.RequestError, httpx.TimeoutException) as e:
                # Network/timeout errors, retry with exponential backoff
                last_exception = e
                if attempt < MAX_RETRIES and retry:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    time.sleep(delay)
                    continue
                raise

        # Should not reach here, but handle it
        if last_exception:
            raise last_exception
        raise RuntimeError("Request failed after retries")

    def create_subscription(
        self,
        webhook_url: str,
        calendar_id: str = "primary",
        expiration_minutes: int = 4230,  # ~3 days (max allowed)
    ) -> tuple[str, datetime, str]:
        """Create a webhook subscription for calendar events.

        Args:
            webhook_url: Public HTTPS URL for webhook notifications
            calendar_id: Calendar ID (default: "primary")
            expiration_minutes: Minutes until subscription expires (max 4230)

        Returns:
            Tuple of (subscription_id, expiration_datetime, client_state)

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        # Generate random clientState for webhook verification
        client_state = str(uuid.uuid4())

        # Calculate expiration time
        expiration_time = datetime.now(UTC) + timedelta(minutes=expiration_minutes)

        # Prepare subscription request
        subscription_data = {
            "changeType": "created,updated,deleted",
            "notificationUrl": webhook_url,
            "resource": f"/me/calendars/{calendar_id}/events",
            "expirationDateTime": expiration_time.isoformat(),
            "clientState": client_state,
        }

        response = self._request("POST", "/subscriptions", json=subscription_data)
        data = response.json()

        subscription_id = data["id"]
        expiry_str = data["expirationDateTime"]
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))

        return subscription_id, expiry, client_state

    def renew_subscription(
        self,
        subscription_id: str,
        expiration_minutes: int = 4230,  # ~3 days (max allowed)
    ) -> tuple[datetime, str]:
        """Renew a webhook subscription and rotate clientState.

        Args:
            subscription_id: ID of subscription to renew
            expiration_minutes: Minutes until subscription expires (max 4230)

        Returns:
            Tuple of (new_expiration_datetime, new_client_state)

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        # Generate new clientState for rotation (security best practice)
        new_client_state = str(uuid.uuid4())

        # Calculate new expiration time
        expiration_time = datetime.now(UTC) + timedelta(minutes=expiration_minutes)

        # Prepare renewal request
        renewal_data = {
            "expirationDateTime": expiration_time.isoformat(),
            "clientState": new_client_state,
        }

        response = self._request(
            "PATCH",
            f"/subscriptions/{subscription_id}",
            json=renewal_data,
        )
        data = response.json()

        expiry_str = data["expirationDateTime"]
        expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))

        return expiry, new_client_state

    def delete_subscription(self, subscription_id: str) -> None:
        """Delete a webhook subscription.

        Args:
            subscription_id: ID of subscription to delete

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        self._request("DELETE", f"/subscriptions/{subscription_id}")

    def delta_sync(
        self,
        delta_link: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Process delta sync for calendar events.

        Args:
            delta_link: Delta link from previous sync

        Returns:
            Tuple of (events_list, new_delta_link)

        Raises:
            httpx.HTTPStatusError: On API errors (including 410 Gone for expired delta)
        """
        response = self._request("GET", delta_link)
        data = response.json()

        events = data.get("value", [])
        new_delta_link = data.get("@odata.deltaLink", delta_link)

        return events, new_delta_link

    def list_events(
        self,
        calendar_id: str = "primary",
        time_min: datetime | None = None,
        time_max: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """List calendar events (full sync fallback).

        This method performs a full sync and returns a delta link for future
        incremental syncs. Use this as fallback when delta_sync returns 410 Gone.

        Args:
            calendar_id: Calendar ID (default: "primary")
            time_min: Start time for event filter (optional)
            time_max: End time for event filter (optional)

        Returns:
            Tuple of (events_list, delta_link)

        Raises:
            httpx.HTTPStatusError: On API errors
        """
        # Build query parameters
        params = {}
        if time_min:
            params["startDateTime"] = time_min.isoformat()
        if time_max:
            params["endDateTime"] = time_max.isoformat()

        # Request with delta tracking
        endpoint = f"/me/calendars/{calendar_id}/events/delta"
        response = self._request("GET", endpoint, params=params)
        data = response.json()

        events = data.get("value", [])
        delta_link = data.get("@odata.deltaLink", "")

        return events, delta_link
