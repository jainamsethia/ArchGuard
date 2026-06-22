"""Unit tests for the OpenAI Advisor Provider."""

import json
from unittest.mock import patch, MagicMock
import httpx
import pytest

from archguard.llm.openai_provider import OpenAIAdvisorProvider


@pytest.fixture
def provider():
    return OpenAIAdvisorProvider(api_key="test-key")


def test_openai_provider_success(provider):
    """Test successful generation of recommendations."""
    mock_response_data = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "recommendations": [
                                {
                                    "title": "Fix cycles",
                                    "description": "Remove circular imports",
                                    "severity": "high",
                                    "expected_impact": "Better maintainability",
                                    "priority_score": 85,
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        recs = provider.generate_recommendations("Test context")

        assert len(recs) == 1
        assert recs[0].title == "Fix cycles"
        assert recs[0].severity == "high"
        assert recs[0].priority_score == 85


def test_openai_provider_missing_api_key():
    """Test behavior when API key is missing."""
    empty_provider = OpenAIAdvisorProvider(api_key="")
    recs = empty_provider.generate_recommendations("Context")
    assert recs == []


def test_openai_provider_timeout(provider):
    """Test handling of HTTP timeouts."""
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Timeout")):
        recs = provider.generate_recommendations("Context")
        assert recs == []


def test_openai_provider_rate_limit(provider):
    """Test handling of 429 Rate Limit error."""
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Rate Limit", request=MagicMock(), response=mock_resp
        )
        mock_post.return_value = mock_resp

        recs = provider.generate_recommendations("Context")
        assert recs == []


def test_openai_provider_malformed_json(provider):
    """Test handling of invalid JSON from LLM."""
    mock_response_data = {
        "choices": [{"message": {"content": "This is not JSON at all."}}]
    }
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_post.return_value = mock_resp

        recs = provider.generate_recommendations("Context")
        assert recs == []


def test_openai_provider_missing_fields_and_validation(provider):
    """Test parsing of recommendations with missing/invalid fields."""
    mock_response_data = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "recommendations": [
                                # Valid
                                {
                                    "title": "Valid Rec",
                                    "description": "Good",
                                    "severity": "CRITICAL",  # Should be lowercased
                                    "expected_impact": "Good impact",
                                    "priority_score": "150",  # Should be capped at 100
                                },
                                # Missing required fields
                                {"title": "Missing desc and impact"},
                                # Invalid severity fallback
                                {
                                    "title": "Bad Severity",
                                    "description": "Desc",
                                    "severity": "super-bad",  # Fallback to medium
                                    "expected_impact": "Impact",
                                    "priority_score": "not_an_int",  # Fallback to 50
                                },
                            ]
                        }
                    )
                }
            }
        ]
    }
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_post.return_value = mock_resp

        recs = provider.generate_recommendations("Context")

        assert len(recs) == 2  # The second item should be skipped

        assert recs[0].title == "Valid Rec"
        assert recs[0].severity == "critical"
        assert recs[0].priority_score == 100

        assert recs[1].title == "Bad Severity"
        assert recs[1].severity == "medium"
        assert recs[1].priority_score == 50
