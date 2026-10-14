"""Tests for the GitHub-identity user model and OAuth handoff codes."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import OAuthHandoff

User = get_user_model()

TOKEN = "gho_a_real_looking_token"


class TestUserManager:
    def test_create_user_uses_an_unusable_password(self, db: Any) -> None:
        account = User.objects.create_user(username="dave")
        assert not account.has_usable_password()

    def test_create_user_requires_a_username(self, db: Any) -> None:
        with pytest.raises(ValueError, match="GitHub username"):
            User.objects.create_user(username="")

    def test_duplicate_username_is_rejected(self, db: Any) -> None:
        User.objects.create_user(username="dave")

        with pytest.raises(ValidationError):
            User.objects.create_user(username="dave")

    def test_superuser_gets_staff_and_superuser(self, db: Any) -> None:
        admin = User.objects.create_superuser(username="root", email="root@example.com")

        assert admin.is_staff
        assert admin.is_superuser

    def test_natural_key_lookup_is_case_insensitive(self, db: Any) -> None:
        User.objects.create_user(username="MixedCase")
        assert User.objects.get_by_natural_key("mixedcase").username == "MixedCase"

    def test_natural_key_lookup_without_a_username_raises(self, db: Any) -> None:
        with pytest.raises(User.DoesNotExist):
            User.objects.get_by_natural_key(None)


class TestGitHubTokenStorage:
    def test_token_is_encrypted_at_rest(self, db: Any) -> None:
        account = User.objects.create_user(username="erin")
        account.store_github_token(access_token=TOKEN, scope="repo")
        account.save()

        # Re-read from the database, bypassing the in-memory instance.
        stored = User.objects.get(pk=account.pk)
        assert TOKEN not in stored.access_token_enc
        assert stored.access_token_enc != ""
        assert stored.github_access_token == TOKEN

    def test_connected_flag_reflects_stored_token(self, db: Any) -> None:
        account = User.objects.create_user(username="frank")
        assert account.is_github_connected is False
        account.store_github_token(access_token=TOKEN)
        assert account.is_github_connected is True

    def test_clear_token_disconnects_the_account(self, db: Any) -> None:
        account = User.objects.create_user(username="grace")
        account.store_github_token(access_token=TOKEN, refresh_token="r")
        account.clear_github_token()

        assert account.is_github_connected is False
        assert account.github_access_token == ""
        assert account.github_refresh_token == ""
        assert account.github_connected_at is None

    def test_expiry_is_computed_from_expires_in(self, db: Any) -> None:
        account = User.objects.create_user(username="heidi")
        account.store_github_token(access_token=TOKEN, expires_in=3600)
        assert account.token_expires_at is not None
        assert account.token_expired() is False

    def test_expired_token_is_reported_as_expired(self, db: Any) -> None:
        account = User.objects.create_user(username="ivan")
        account.store_github_token(access_token=TOKEN, expires_in=1)
        account.token_expires_at = timezone.now() - timedelta(seconds=1)
        assert account.token_expired() is True

    def test_token_without_expiry_never_expires(self, db: Any) -> None:
        account = User.objects.create_user(username="judy")
        account.store_github_token(access_token=TOKEN)
        assert account.token_expires_at is None
        assert account.token_expired() is False

    def test_display_name_prefers_full_name(self, db: Any) -> None:
        assert User.objects.create_user(username="karl").display_name == "karl"
        assert (
            User.objects.create_user(username="lars", full_name="Lars O").display_name == "Lars O"
        )


class TestOAuthHandoff:
    def test_issued_code_redeems_for_its_user(self, db: Any) -> None:
        account = User.objects.create_user(username="mallory")
        code = OAuthHandoff.issue(account)
        assert OAuthHandoff.redeem(code).pk == account.pk

    def test_raw_code_is_never_stored(self, db: Any) -> None:
        account = User.objects.create_user(username="nina")
        code = OAuthHandoff.issue(account)
        stored = OAuthHandoff.objects.get()
        assert code not in stored.code_hash

    def test_a_code_can_only_be_redeemed_once(self, db: Any) -> None:
        account = User.objects.create_user(username="olivia")
        code = OAuthHandoff.issue(account)
        OAuthHandoff.redeem(code)

        with pytest.raises(OAuthHandoff.DoesNotExist):
            OAuthHandoff.redeem(code)

    def test_expired_code_cannot_be_redeemed(self, db: Any) -> None:
        account = User.objects.create_user(username="peggy")
        code = OAuthHandoff.issue(account)
        handoff = OAuthHandoff.objects.get()
        handoff.expires_at = timezone.now() - timedelta(seconds=1)
        handoff.save(update_fields=["expires_at"])

        with pytest.raises(OAuthHandoff.DoesNotExist):
            OAuthHandoff.redeem(code)

    def test_unknown_code_cannot_be_redeemed(self, db: Any) -> None:
        with pytest.raises(OAuthHandoff.DoesNotExist):
            OAuthHandoff.redeem("this-code-was-never-issued")

    def test_purge_expired_removes_only_stale_rows(self, db: Any) -> None:
        account = User.objects.create_user(username="trent")
        stale = OAuthHandoff.objects.create(
            user=account,
            code_hash="a" * 64,
            expires_at=timezone.now() - timedelta(hours=2),
        )
        fresh = OAuthHandoff.objects.create(
            user=account,
            code_hash="b" * 64,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        removed = OAuthHandoff.purge_expired()

        assert removed == 1
        assert not OAuthHandoff.objects.filter(pk=stale.pk).exists()
        assert OAuthHandoff.objects.filter(pk=fresh.pk).exists()
