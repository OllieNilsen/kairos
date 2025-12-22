"""AWS SSM Parameter Store adapter for secrets."""

from __future__ import annotations

from functools import lru_cache

import boto3


@lru_cache(maxsize=16)
def get_parameter(name: str, decrypt: bool = True) -> str:
    """Fetch a parameter from SSM Parameter Store.

    Uses LRU cache to avoid repeated API calls within the same Lambda invocation.

    Args:
        name: The parameter name (e.g., "/kairos/bland-api-key")
        decrypt: Whether to decrypt SecureString parameters

    Returns:
        The parameter value

    Raises:
        botocore.exceptions.ClientError: If parameter doesn't exist or access denied
    """
    client = boto3.client("ssm")
    response = client.get_parameter(Name=name, WithDecryption=decrypt)
    return response["Parameter"]["Value"]


def clear_cache() -> None:
    """Clear the parameter cache. Useful for testing."""
    get_parameter.cache_clear()
