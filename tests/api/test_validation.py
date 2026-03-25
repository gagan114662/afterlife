"""Tests for input validation on Pydantic models."""
import pytest
from pydantic import ValidationError

from services.api.main import MessageRequest, StartRequest
from services.api.sanitize import sanitize_name


def test_rejects_empty_contact_name():
    with pytest.raises(ValidationError):
        StartRequest(contact_name="", user_name="Gagan")


def test_rejects_oversized_contact_name():
    with pytest.raises(ValidationError):
        StartRequest(contact_name="x" * 101, user_name="Gagan")


def test_rejects_empty_message():
    with pytest.raises(ValidationError):
        MessageRequest(
            session_id="a" * 36,
            message="",
        )


def test_rejects_oversized_message():
    with pytest.raises(ValidationError):
        MessageRequest(
            session_id="a" * 36,
            message="x" * 2001,
        )


def test_sanitize_strips_injection():
    result = sanitize_name('mom"; DROP TABLE contacts; --')
    assert '"' not in result
    assert ";" not in result


def test_sanitize_allows_safe_names():
    assert sanitize_name("Grandma Rose") == "Grandma Rose"
    assert sanitize_name("O'Brien") == "O'Brien"


def test_valid_start_request():
    req = StartRequest(contact_name="mom", user_name="Gagan")
    assert req.contact_name == "mom"
    assert req.user_name == "Gagan"


def test_valid_message_request():
    req = MessageRequest(session_id="a" * 36, message="Hello!")
    assert req.message == "Hello!"
