"""Custom user model: a GitHub identity.

CodePilotAI has no password login. A user *is* their GitHub account, so the
username is the GitHub login and the OAuth token is the only credential stored.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.crypto import decrypt_token, encrypt_token


class UserManager(BaseUserManager["User"]):
    """Manager for the GitHub-identity user model."""

    use_in_migrations = True

    def create_user(self, username: str, email: str = "", **extra_fields: Any) -> User:
        """Create and return a user keyed by GitHub login."""
        if not username:
            raise ValueError("A GitHub username is required.")
        user = self.model(username=username, email=self.normalize_email(email), **extra_fields)
        user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_superuser(self, username: str, email: str = "", **extra_fields: Any) -> User:
        """Create and return a user with staff/superuser privileges."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields.get("is_staff") or not extra_fields.get("is_superuser"):
            raise ValueError("A superuser must have is_staff and is_superuser set to True.")
        return self.create_user(username, email, **extra_fields)

    def get_by_natural_key(self, username: str | None) -> User:
        """Look a user up by GitHub login, case-insensitively (GitHub logins are)."""
        if not username:
            raise self.model.DoesNotExist()
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


class User(AbstractBaseUser, PermissionsMixin):
    """A CodePilotAI user, identified by their GitHub account."""

    # GitHub's numeric account id. Stable across renames, which is why it is the
    # real identity key -- `username` changes when someone renames their account.
    github_id: models.BigIntegerField = models.BigIntegerField(
        unique=True,
        null=True,
        blank=True,
        help_text="GitHub numeric account id. The stable primary identifier.",
    )
    username: models.CharField = models.CharField(
        max_length=150,
        unique=True,
        help_text="GitHub login. Changes if the user renames their GitHub account.",
    )
    email: models.EmailField = models.EmailField(blank=True)
    full_name: models.CharField = models.CharField(max_length=255, blank=True)
    avatar_url: models.URLField = models.URLField(max_length=500, blank=True)

    # ── Encrypted GitHub credentials ────────────────────────────────────────
    access_token_enc: models.TextField = models.TextField(
        blank=True,
        default="",
        help_text="Fernet-encrypted GitHub OAuth access token. Never expose this.",
    )
    refresh_token_enc: models.TextField = models.TextField(blank=True, default="")
    token_scope: models.CharField = models.CharField(max_length=500, blank=True, default="")
    token_expires_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)
    github_connected_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)

    # ── Permissions ─────────────────────────────────────────────────────────
    is_active: models.BooleanField = models.BooleanField(
        default=True,
        help_text="Designates whether this user can authenticate and use the API.",
    )
    is_staff: models.BooleanField = models.BooleanField(
        default=False,
        help_text="Designates access to the Django admin site.",
    )
    date_joined: models.DateTimeField = models.DateTimeField(default=timezone.now)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        db_table = "accounts_user"
        ordering = ("-date_joined",)
        indexes = [
            models.Index(fields=("username",), name="accounts_username_idx"),
            models.Index(fields=("-date_joined",), name="accounts_joined_idx"),
        ]
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return f"@{self.username}"

    # ── Token handling ──────────────────────────────────────────────────────
    @property
    def github_access_token(self) -> str:
        """Decrypt and return the stored GitHub access token (empty if unusable)."""
        return decrypt_token(self.access_token_enc)

    @property
    def github_refresh_token(self) -> str:
        """Decrypt and return the stored GitHub refresh token."""
        return decrypt_token(self.refresh_token_enc)

    def store_github_token(
        self,
        *,
        access_token: str,
        refresh_token: str = "",
        scope: str = "",
        expires_in: int | None = None,
    ) -> None:
        """Encrypt and persist a GitHub OAuth token pair."""
        self.access_token_enc = encrypt_token(access_token)
        self.refresh_token_enc = encrypt_token(refresh_token)
        self.token_scope = scope
        if expires_in:
            self.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
        self.github_connected_at = timezone.now()

    def clear_github_token(self) -> None:
        """Revoke stored credentials (sign out / disconnect)."""
        self.access_token_enc = ""
        self.refresh_token_enc = ""
        self.token_scope = ""
        self.token_expires_at = None
        self.github_connected_at = None

    @property
    def is_github_connected(self) -> bool:
        """True when a usable GitHub token is stored."""
        return bool(self.access_token_enc)

    def token_expired(self, leeway_seconds: int = 60) -> bool:
        """True when the token is expired or about to expire."""
        if self.token_expires_at is None:
            return False
        return timezone.now() >= self.token_expires_at - timedelta(seconds=leeway_seconds)

    @property
    def display_name(self) -> str:
        """Human-friendly name for UI: full name, else GitHub login."""
        return self.full_name or self.username


class OAuthHandoff(models.Model):
    """A short-lived, single-use code that carries a freshly issued JWT pair.

    After GitHub redirects back, the app receives an opaque one-time code and
    exchanges it for JWTs over POST. Keeping the JWTs out of the redirect URL
    means they never reach browser history, ``Referer`` headers, or web-server
    access logs.

    Only a SHA-256 hash of the code is stored, so a database leak cannot be
    replayed against the API.
    """

    HANDOFF_TTL_SECONDS = 180

    code_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 of the raw code. The raw code is never persisted.",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_handoffs",
    )
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_oauth_handoff"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("expires_at",), name="handoff_expires_idx"),
        ]
        verbose_name = "OAuth handoff"
        verbose_name_plural = "OAuth handoffs"

    def __str__(self) -> str:
        return f"handoff<user={self.user_id}>"

    @staticmethod
    def hash_code(raw_code: str) -> str:
        """Return the stored hash for a raw code."""
        return hashlib.sha256(raw_code.encode()).hexdigest()

    @classmethod
    def issue(cls, user: Any) -> str:
        """Create a handoff for ``user`` and return the raw one-time code."""
        raw_code = secrets.token_urlsafe(32)
        cls.objects.create(
            user=user,
            code_hash=cls.hash_code(raw_code),
            expires_at=timezone.now() + timedelta(seconds=cls.HANDOFF_TTL_SECONDS),
        )
        return raw_code

    @property
    def is_usable(self) -> bool:
        """True when the code is unconsumed and unexpired."""
        return self.consumed_at is None and self.expires_at > timezone.now()

    def consume(self) -> None:
        """Mark this code used so it cannot be replayed."""
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])

    @classmethod
    def redeem(cls, raw_code: str) -> User:
        """Atomically redeem a raw code and return its user.

        ``select_for_update`` ensures two simultaneous redemptions cannot both
        succeed. Raises ``DoesNotExist`` for unknown, expired, or already-used
        codes -- the caller maps all three to one opaque error.
        """
        with transaction.atomic():
            handoff = (
                cls.objects.select_for_update()
                .select_related("user")
                .filter(code_hash=cls.hash_code(raw_code))
                .first()
            )
            if handoff is None or not handoff.is_usable:
                raise cls.DoesNotExist("OAuth handoff code is invalid, expired, or already used.")
            handoff.consume()
            return handoff.user

    @classmethod
    def purge_expired(cls, older_than: timedelta | None = None) -> int:
        """Delete handoffs that expired before ``older_than``. Returns rows removed."""
        cutoff = timezone.now() - (older_than or timedelta(hours=1))
        deleted, _ = cls.objects.filter(expires_at__lt=cutoff).delete()
        return deleted
