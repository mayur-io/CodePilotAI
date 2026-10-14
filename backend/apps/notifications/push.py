"""Firebase Cloud Messaging (FCM) delivery for task-status notifications.

The transport is a thin, injectable seam so the rest of the backend never talks
to Google directly:

* :class:`PushTransport` -- one method, :meth:`send`;
* :class:`NullPushTransport` -- the default. It logs and drops, so a deployment
  with no Firebase project configured behaves exactly as it did before push
  existed, and the whole test suite stays offline;
* :class:`FcmPushTransport` -- the real thing. It speaks the FCM HTTP v1 API and
  signs its own OAuth2 assertion from a Google service-account key, so the
  backend needs no extra SDK dependency (the same reasoning that hand-rolls the
  unified-diff parser instead of pulling in ``unidiff``).

Credentials live in settings and are blank by default (Task.md: keys stay
placeholders). ``FCM_SERVICE_ACCOUNT_JSON`` may hold the raw service-account
JSON inline; otherwise ``FCM_SERVICE_ACCOUNT_FILE`` points at it on disk.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings

from apps.agent_tasks.models import AgentTask
from apps.notifications.models import DeviceToken
from config.observability import get_logger

logger = get_logger(__name__)

#: OAuth2 scope that allows sending FCM messages.
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
#: Android notification channel the client creates; must match the app.
ANDROID_CHANNEL_ID = "codepilot_tasks"
_JWT_TTL_SECONDS = 3600
#: Refresh the OAuth token this many seconds before Google expires it.
_TOKEN_LEEWAY_SECONDS = 60

#: Statuses worth waking a phone for: the run needs the user's decision, or it
#: reached a terminal outcome the user is waiting on. ``cancelled`` is excluded
#: -- the user cancelled it themselves -- as are all in-flight states.
PUSH_NOTIFY_STATUSES: frozenset[str] = frozenset(
    {
        AgentTask.Status.AWAITING_APPROVAL,
        AgentTask.Status.PUSHED,
        AgentTask.Status.MERGED,
        AgentTask.Status.FAILED,
    }
)

__all__: list[str] = [
    "ANDROID_CHANNEL_ID",
    "FCM_SCOPE",
    "PUSH_NOTIFY_STATUSES",
    "FcmPushTransport",
    "NullPushTransport",
    "PushMessage",
    "PushTransport",
    "build_transport",
    "get_transport",
    "notify_task_status",
    "notify_user",
    "set_transport",
]


@dataclass(frozen=True)
class PushMessage:
    """One FCM message addressed to one registration token."""

    token: str
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)


class PushTransport(Protocol):
    """Delivers a batch of messages, returning how many were accepted."""

    def send(self, messages: Sequence[PushMessage]) -> int: ...


class NullPushTransport:
    """Default transport: drop messages and say so, quietly."""

    def send(self, messages: Sequence[PushMessage]) -> int:
        """Log the batch size; deliver nothing."""
        if messages:
            logger.info("push_dropped_no_transport", count=len(messages))
        return 0


class FcmPushTransport:
    """Deliver messages through the FCM HTTP v1 API.

    A :class:`FcmPushTransport` is built per push batch, so its OAuth token
    cache only has to survive the batch: a user with several devices pays for a
    single token exchange. Call :meth:`close` when done.
    """

    def __init__(
        self,
        *,
        project_id: str,
        service_account: Mapping[str, Any],
        base_url: str,
        token_uri: str,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._project_id = project_id
        self._service_account = dict(service_account)
        self._base_url = base_url.rstrip("/")
        self._token_uri = token_uri
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self._cached_token: str = ""
        self._cached_token_expiry: float = 0.0

    def __enter__(self) -> FcmPushTransport:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection pool if this transport created it."""
        if self._owns_client:
            self._client.close()

    # ── sending ─────────────────────────────────────────────────────────────
    def send(self, messages: Sequence[PushMessage]) -> int:
        """Send each message individually; FCM v1 has no multi-send endpoint.

        A per-message failure is logged and skipped: one dead token (an
        uninstalled app) must not stop delivery to the user's other devices.
        """
        if not messages:
            return 0

        token = self._access_token()
        delivered = 0
        for message in messages:
            body = {"message": self._envelope(message)}
            try:
                response = self._client.post(
                    self._send_url(),
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                logger.warning("fcm_send_failed", error=str(exc))
                continue
            if response.is_success:
                delivered += 1
            else:
                logger.warning(
                    "fcm_send_rejected",
                    status_code=response.status_code,
                    detail=response.text[:300],
                )
        return delivered

    def _send_url(self) -> str:
        return f"{self._base_url}/v1/projects/{self._project_id}/messages:send"

    @staticmethod
    def _envelope(message: PushMessage) -> dict[str, Any]:
        """The FCM v1 ``message`` object for one push.

        Data-only on purpose: the client builds the notification itself, so it
        controls the channel, the icon, and the deep link. A ``notification``
        block would make the SDK post its own notification while the app is
        backgrounded and never call our service, and the tap would carry no task
        id. Title/body ride inside ``data`` alongside the routing ids.
        """
        data = {"title": message.title, "body": message.body, "channel_id": ANDROID_CHANNEL_ID}
        data.update({key: str(value) for key, value in message.data.items()})
        return {
            "token": message.token,
            "data": data,
            # High priority lets a data-only message wake a backgrounded app.
            "android": {"priority": "high"},
        }

    # ── OAuth2 ──────────────────────────────────────────────────────────────
    def _access_token(self) -> str:
        """Return a cached OAuth2 bearer token, minting one if needed."""
        now = time.monotonic()
        if self._cached_token and now < self._cached_token_expiry:
            return self._cached_token

        assertion = sign_service_account_assertion(self._service_account, self._token_uri)
        response = self._client.post(
            self._token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self._cached_token = str(payload["access_token"])
        expires_in = int(payload.get("expires_in", _JWT_TTL_SECONDS))
        self._cached_token_expiry = now + max(1.0, expires_in - _TOKEN_LEEWAY_SECONDS)
        return self._cached_token


# ── JWT signing (hand-rolled: no google-auth dependency) ─────────────────────


def sign_service_account_assertion(
    service_account: Mapping[str, Any],
    token_uri: str,
    *,
    issued_at: int | None = None,
) -> str:
    """Build a signed RS256 assertion for Google's JWT-bearer OAuth flow.

    ``issued_at`` is injectable so tests can freeze the clock; production always
    uses the wall clock.
    """
    issued = int(time.time()) if issued_at is None else issued_at
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": str(service_account["client_email"]),
        "scope": FCM_SCOPE,
        "aud": token_uri,
        "iat": issued,
        "exp": issued + _JWT_TTL_SECONDS,
    }
    signing_input = f"{_b64url_json(header)}.{_b64url_json(claims)}"
    private_key = serialization.load_pem_private_key(
        str(service_account["private_key"]).encode("utf-8"),
        password=None,
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("The FCM service-account key must be an RSA private key.")
    signature = private_key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def _b64url(data: bytes) -> str:
    """URL-safe base64 without padding, as JWT requires."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_json(payload: Mapping[str, Any]) -> str:
    return _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


# ── transport selection ──────────────────────────────────────────────────────


def load_service_account() -> dict[str, Any] | None:
    """Read the service-account JSON from settings, or ``None`` if unset.

    Both failure modes (a missing file, malformed JSON) log and degrade to
    "not configured" rather than raising: a broken credential must disable push,
    never fail a task run.
    """
    raw = settings.FCM_SERVICE_ACCOUNT_JSON
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            logger.warning("fcm_service_account_json_invalid")
            return None
    path = settings.FCM_SERVICE_ACCOUNT_FILE
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        logger.warning("fcm_service_account_file_missing", path=path)
        return None
    except ValueError:
        logger.warning("fcm_service_account_file_invalid", path=path)
        return None


def build_transport() -> PushTransport:
    """Build the transport implied by settings: FCM when configured, else null."""
    if not settings.FCM_ENABLED:
        return NullPushTransport()

    account = load_service_account()
    project_id = settings.FCM_PROJECT_ID or str((account or {}).get("project_id") or "")
    if account is None or not project_id:
        return NullPushTransport()

    return FcmPushTransport(
        project_id=project_id,
        service_account=account,
        base_url=settings.FCM_BASE_URL,
        token_uri=settings.FCM_TOKEN_URI,
        timeout=float(settings.FCM_TIMEOUT_SECONDS),
    )


_transport_override: PushTransport | None = None


def set_transport(transport: PushTransport | None) -> None:
    """Install a transport for tests, or ``None`` to restore the configured one."""
    global _transport_override
    _transport_override = transport


def get_transport() -> PushTransport:
    """Return the transport to use: a test override, else one built from settings."""
    if _transport_override is not None:
        return _transport_override
    return build_transport()


# ── dispatch ─────────────────────────────────────────────────────────────────


def notify_user(
    user: Any,
    *,
    title: str,
    body: str,
    data: Mapping[str, str] | None = None,
) -> int:
    """Push one notification to every device ``user`` has registered."""
    tokens = list(DeviceToken.objects.filter(user=user).values_list("token", flat=True))
    if not tokens:
        return 0

    payload = dict(data or {})
    messages = [PushMessage(token=token, title=title, body=body, data=payload) for token in tokens]
    transport = get_transport()
    try:
        return transport.send(messages)
    finally:
        # A configured transport owns an HTTP connection pool; an injected test
        # double has no ``close``. Either way, never leak the batch's socket.
        close = getattr(transport, "close", None)
        if callable(close):
            close()


def task_push_copy(task: AgentTask, status: str) -> tuple[str, str] | None:
    """Title and body for ``status``, or ``None`` when it is not worth a push."""
    label = (task.title or task.prompt or "Your task").strip()
    label = label[:120]

    if status == AgentTask.Status.AWAITING_APPROVAL:
        return "Changes ready to review", f"“{label}” is waiting for your approval."
    if status == AgentTask.Status.PUSHED:
        return "Changes pushed", f"“{label}” opened a pull request."
    if status == AgentTask.Status.MERGED:
        return "Pull request merged", f"“{label}” was merged."
    if status == AgentTask.Status.FAILED:
        detail = (task.error_message or "The run did not finish.").strip().splitlines()[0]
        return "Task failed", f"“{label}”: {detail[:160]}"
    return None


def notify_task_status(task_id: int, status: str) -> int:
    """Push the user whose task reached ``status``. Best-effort: never raises.

    Called from the event stream for every notify-worthy status change, so a
    dead credential or an FCM outage can only mean "no notification" -- it can
    never turn a successful run into a failed one.
    """
    try:
        task = AgentTask.objects.select_related("user", "repository").filter(pk=task_id).first()
        if task is None:
            return 0
        copy = task_push_copy(task, status)
        if copy is None:
            return 0
        title, body = copy
        return notify_user(
            task.user,
            title=title,
            body=body,
            data={
                "type": "task_status",
                "task_id": str(task.pk),
                "status": status,
                "repository": task.repository.full_name,
                "pr_url": task.pr_url or "",
            },
        )
    except Exception:
        logger.exception("push_dispatch_failed", task_id=task_id, status=status)
        return 0
