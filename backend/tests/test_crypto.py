"""Tests for Fernet token encryption and secret scrubbing."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from apps.accounts.crypto import decrypt_token, encrypt_token, generate_key, scrub_secrets

SECRET = "gho_super_sensitive_token_value"


class TestTokenEncryption:
    def test_round_trip_preserves_the_token(self) -> None:
        assert decrypt_token(encrypt_token(SECRET)) == SECRET

    def test_ciphertext_does_not_contain_the_plaintext(self) -> None:
        assert SECRET not in encrypt_token(SECRET)

    def test_ciphertext_is_deterministic_per_fernet_but_not_plain(self) -> None:
        # Fernet includes a random IV, so two encryptions differ while both
        # decrypt to the same value.
        first = encrypt_token(SECRET)
        second = encrypt_token(SECRET)
        assert first != second
        assert decrypt_token(first) == decrypt_token(second) == SECRET

    def test_empty_input_stays_empty(self) -> None:
        assert encrypt_token("") == ""
        assert decrypt_token("") == ""

    def test_undecryptable_value_returns_empty_instead_of_raising(self) -> None:
        # A rotated key must degrade to "user must re-authenticate", not a 500.
        assert decrypt_token("not-a-valid-fernet-token") == ""

    def test_token_encrypted_with_a_different_key_is_unreadable(self) -> None:
        other = Fernet(generate_key().encode())
        foreign = other.encrypt(SECRET.encode()).decode()
        assert decrypt_token(foreign) == ""


class TestScrubSecrets:
    def test_known_secret_is_replaced(self) -> None:
        message = f"remote: failed with token {SECRET} in header"
        cleaned = scrub_secrets(message, SECRET)
        assert SECRET not in cleaned
        assert "***REDACTED***" in cleaned

    def test_multiple_secrets_are_all_replaced(self) -> None:
        cleaned = scrub_secrets(f"{SECRET} and other_secret_here", SECRET, "other_secret_here")
        assert "***REDACTED***" in cleaned
        assert SECRET not in cleaned
        assert "other_secret_here" not in cleaned

    def test_short_or_none_values_are_ignored(self) -> None:
        # Guarding on length avoids redacting common substrings across a message.
        assert scrub_secrets("abc appears here", "abc") == "abc appears here"
        assert scrub_secrets("abc appears here", None) == "abc appears here"
        assert scrub_secrets("abc appears here", "") == "abc appears here"

    def test_message_without_the_secret_is_unchanged(self) -> None:
        message = "fatal: repository not found"
        assert scrub_secrets(message, SECRET) == message


@pytest.mark.parametrize("token", ["ghp_a", "x", "123456"])
def test_short_values_are_not_used_as_scrub_patterns(token: str) -> None:
    """Anything 6 characters or shorter is ignored.

    GitHub tokens are far longer than this, so the guard only exists to stop a
    stub or sentinel value from shredding an unrelated log line.
    """
    assert scrub_secrets(f"prefix {token} suffix", token) == f"prefix {token} suffix"


def test_the_length_guard_boundary_is_documented_by_this_test() -> None:
    # Seven characters is the first length that gets redacted. If the guard in
    # scrub_secrets ever moves, this test is the thing that should fail loudly.
    assert "***REDACTED***" in scrub_secrets("prefix 1234567 suffix", "1234567")
