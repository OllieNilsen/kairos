"""Unit tests for SSM adapter."""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.ssm import clear_cache, get_parameter


@pytest.fixture(autouse=True)
def clear_ssm_cache():
    """Clear the SSM cache before and after each test."""
    clear_cache()
    yield
    clear_cache()


class TestGetParameter:
    """Tests for get_parameter function."""

    def test_fetches_parameter_value(self):
        """Should fetch and return the parameter value."""
        mock_client = MagicMock()
        mock_client.get_parameter.return_value = {"Parameter": {"Value": "test-api-key-123"}}

        with patch("src.adapters.ssm.boto3.client", return_value=mock_client):
            result = get_parameter("/kairos/test-key")

        assert result == "test-api-key-123"
        mock_client.get_parameter.assert_called_once_with(
            Name="/kairos/test-key", WithDecryption=True
        )

    def test_caches_parameter_value(self):
        """Should cache the value and not call SSM twice."""
        mock_client = MagicMock()
        mock_client.get_parameter.return_value = {"Parameter": {"Value": "cached-value"}}

        with patch("src.adapters.ssm.boto3.client", return_value=mock_client):
            result1 = get_parameter("/kairos/cached-key")
            result2 = get_parameter("/kairos/cached-key")

        assert result1 == "cached-value"
        assert result2 == "cached-value"
        # Should only call SSM once due to caching
        assert mock_client.get_parameter.call_count == 1

    def test_different_params_cached_separately(self):
        """Should cache different parameters separately."""
        mock_client = MagicMock()
        mock_client.get_parameter.side_effect = [
            {"Parameter": {"Value": "value-a"}},
            {"Parameter": {"Value": "value-b"}},
        ]

        with patch("src.adapters.ssm.boto3.client", return_value=mock_client):
            result_a = get_parameter("/kairos/param-a")
            result_b = get_parameter("/kairos/param-b")

        assert result_a == "value-a"
        assert result_b == "value-b"
        assert mock_client.get_parameter.call_count == 2

    def test_decrypt_false_option(self):
        """Should pass WithDecryption=False when decrypt=False."""
        mock_client = MagicMock()
        mock_client.get_parameter.return_value = {"Parameter": {"Value": "plain-value"}}

        with patch("src.adapters.ssm.boto3.client", return_value=mock_client):
            result = get_parameter("/kairos/plain-param", decrypt=False)

        assert result == "plain-value"
        mock_client.get_parameter.assert_called_once_with(
            Name="/kairos/plain-param", WithDecryption=False
        )


class TestClearCache:
    """Tests for clear_cache function."""

    def test_clears_cached_values(self):
        """Should clear the cache so next call fetches fresh."""
        mock_client = MagicMock()
        mock_client.get_parameter.side_effect = [
            {"Parameter": {"Value": "old-value"}},
            {"Parameter": {"Value": "new-value"}},
        ]

        with patch("src.adapters.ssm.boto3.client", return_value=mock_client):
            result1 = get_parameter("/kairos/refresh-key")
            clear_cache()
            result2 = get_parameter("/kairos/refresh-key")

        assert result1 == "old-value"
        assert result2 == "new-value"
        assert mock_client.get_parameter.call_count == 2
