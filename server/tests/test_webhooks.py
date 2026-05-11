"""Tests for GitHub webhook handler: signature verification, auto-update, auto-teardown."""
from __future__ import annotations

import hashlib
import hmac

import pytest

from server.api.webhooks import _verify_signature


def test_valid_signature():
    secret = "test-secret"
    payload = b'{"ref":"refs/heads/main"}'
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert _verify_signature(payload, sig, secret) is True


def test_invalid_signature():
    assert _verify_signature(b"payload", "sha256=wrong", "secret") is False


def test_missing_signature():
    assert _verify_signature(b"payload", None, "secret") is False


def test_empty_secret():
    assert _verify_signature(b"payload", "sha256=whatever", "") is False
