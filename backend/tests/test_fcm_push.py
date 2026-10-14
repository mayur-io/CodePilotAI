"""Tests for FCM push: JWT signing, the HTTP v1 transport, and dispatch.

Everything here is offline. The real FCM endpoints are never called: the
service-account key is generated in-process and ``pytest-httpx`` intercepts the
two outbound HTTP calls (token exchange and message send).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from django.contrib.auth import get_user_model
from pytest_httpx import HTTPXMock

from apps.agent_tasks import events
from apps.agent_tasks.models import AgentTask
from apps.notifications import push
from apps.notifications.models import DeviceToken
from apps.notifications.push import (
    FcmPushTransport,
    NullPushTransport,
    PushMessage,
    build_transport,
    notify_task_status,
    notify_user,
    sign_service_account_assertion,
)
from apps.repositories.models import Repository

User = get_user_model()

FCM_BASE = "https://fcm.example"
TOKEN_URI = "https://oauth.example/token"
PROJECT_ID = "codepilot-test"
SEND_URL = f"{FCM_BASE}/v1/projects/{PROJECT_ID}/messages:send"


# ── fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def rsa_private_key() -> Any:
    """One RSA key for the whole module; generation is comparatively slow."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def service_account(rsa_private_key: Any) -> dict[str, str]:
    """A plausible Google service-account document with a real private key."""
    pem = rsa_private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(
        "ascii"
    )
    return {
        "client_email": "codepilot-push@codepilot-test.iam.gserviceaccount.com",
        "private_key": pem,
        "project_id": PROJECT_ID,
    }


@pytest.fixture(autouse=True)
def _restore_default_transport() -> Iterator[None]:
    """Undo any per-test transport override, even if the test raises."""
    yield
    push.set_transport(None)


class RecordingTransport:
    """Records the batches handed to it instead of sending them."""

    def __init__(self, *, fail: bool = False) -> None:
        self.batches: list[list[PushMessage]] = []
        self._fail = fail

    def send(self, messages: Sequence[PushMessage]) -> int:
        if self._fail:
            raise RuntimeError("transport exploded")
        batch = list(messages)
        self.batches.append(batch)
        return len(batch)

    @property
    def messages(self) -> list[PushMessage]:
        return [message for batch in self.batches for message in batch]


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _fcm_transport(service_account: dict[str, str]) -> FcmPushTransport:
    return FcmPushTransport(
        project_id=PROJECT_ID,
        service_account=service_account,
        base_url=FCM_BASE,
        token_uri=TOKEN_URI,
    )


def _mock_token(httpx_mock: HTTPXMock, token: str = "ya29.fake") -> None:
    httpx_mock.add_response(
        url=TOKEN_URI,
        method="POST",
        json={"access_token": token, "expires_in": 3600, "token_type": "Bearer"},
    )


# ── JWT signing ──────────────────────────────────────────────────────────────


def test_assertion_is_a_verifiable_rs256_jwt(
    rsa_private_key: Any, service_account: dict[str, str]
) -> None:
    token = sign_service_account_assertion(service_account, TOKEN_URI, issued_at=1_700_000_000)
    header_b64, claims_b64, signature_b64 = token.split(".")

    # Raises InvalidSignature if the signing input or padding is wrong.
    rsa_private_key.public_key().verify(
        _b64url_decode(signature_b64),
        f"{header_b64}.{claims_b64}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    assert json.loads(_b64url_decode(header_b64)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_b64url_decode(claims_b64))
    assert claims["iss"] == service_account["client_email"]
    assert claims["aud"] == TOKEN_URI
    assert claims["scope"] == push.FCM_SCOPE
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] - claims["iat"] == 3600


# ── HTTP v1 transport ────────────────────────────────────────────────────────


def test_send_posts_the_v1_envelope(httpx_mock: HTTPXMock, service_account: dict[str, str]) -> None:
    _mock_token(httpx_mock)
    httpx_mock.add_response(url=SEND_URL, method="POST", json={"name": "messages/1"})

    transport = _fcm_transport(service_account)
    try:
        delivered = transport.send(
            [PushMessage(token="dev-1", title="Hi", body="There", data={"task_id": "7"})]
        )
    finally:
        transport.close()

    assert delivered == 1
    sends = [request for request in httpx_mock.get_requests() if str(request.url) == SEND_URL]
    assert len(sends) == 1
    request = sends[0]
    assert request.headers["authorization"] == "Bearer ya29.fake"

    body = json.loads(request.content)
    message = body["message"]
    assert message["token"] == "dev-1"
    assert message["data"]["title"] == "Hi"
    assert message["data"]["body"] == "There"
    assert message["data"]["task_id"] == "7"
    assert message["data"]["channel_id"] == push.ANDROID_CHANNEL_ID
    assert message["android"] == {"priority": "high"}
    assert "notification" not in message


def test_access_token_is_fetched_once_per_batch(
    httpx_mock: HTTPXMock, service_account: dict[str, str]
) -> None:
    _mock_token(httpx_mock)
    httpx_mock.add_response(url=SEND_URL, method="POST", json={"name": "messages/1"})
    httpx_mock.add_response(url=SEND_URL, method="POST", json={"name": "messages/2"})

    transport = _fcm_transport(service_account)
    try:
        delivered = transport.send(
            [
                PushMessage(token="dev-1", title="Hi", body="There"),
                PushMessage(token="dev-2", title="Hi", body="There"),
            ]
        )
    finally:
        transport.close()

    assert delivered == 2
    token_requests = [
        request for request in httpx_mock.get_requests() if str(request.url) == TOKEN_URI
    ]
    assert len(token_requests) == 1


def test_a_rejected_message_does_not_stop_the_batch(
    httpx_mock: HTTPXMock, service_account: dict[str, str]
) -> None:
    _mock_token(httpx_mock)
    httpx_mock.add_response(
        url=SEND_URL,
        method="POST",
        status_code=404,
        json={"error": {"status": "UNREGISTERED"}},
    )
    httpx_mock.add_response(url=SEND_URL, method="POST", json={"name": "messages/2"})

    transport = _fcm_transport(service_account)
    try:
        delivered = transport.send(
            [
                PushMessage(token="dead", title="Hi", body="There"),
                PushMessage(token="alive", title="Hi", body="There"),
            ]
        )
    finally:
        transport.close()

    assert delivered == 1


def test_empty_batch_makes_no_request(
    httpx_mock: HTTPXMock, service_account: dict[str, str]
) -> None:
    transport = _fcm_transport(service_account)
    try:
        assert transport.send([]) == 0
    finally:
        transport.close()

    assert httpx_mock.get_requests() == []


# ── transport selection ──────────────────────────────────────────────────────


def test_transport_is_null_without_credentials(settings: Any) -> None:
    settings.FCM_PROJECT_ID = ""
    settings.FCM_SERVICE_ACCOUNT_JSON = ""
    settings.FCM_SERVICE_ACCOUNT_FILE = ""

    assert isinstance(build_transport(), NullPushTransport)


def test_transport_is_fcm_when_configured(settings: Any, service_account: dict[str, str]) -> None:
    settings.FCM_PROJECT_ID = PROJECT_ID
    settings.FCM_SERVICE_ACCOUNT_JSON = json.dumps(service_account)

    transport = build_transport()
    assert isinstance(transport, FcmPushTransport)
    transport.close()


def test_project_id_can_come_from_the_service_account(settings: Any, service_account: Any) -> None:
    settings.FCM_PROJECT_ID = ""
    settings.FCM_SERVICE_ACCOUNT_JSON = json.dumps(service_account)

    transport = build_transport()
    assert isinstance(transport, FcmPushTransport)
    transport.close()


def test_disable_switch_forces_the_null_transport(
    settings: Any, service_account: dict[str, str]
) -> None:
    settings.FCM_ENABLED = False
    settings.FCM_PROJECT_ID = PROJECT_ID
    settings.FCM_SERVICE_ACCOUNT_JSON = json.dumps(service_account)

    assert isinstance(build_transport(), NullPushTransport)


def test_malformed_credential_degrades_to_null(settings: Any) -> None:
    settings.FCM_PROJECT_ID = PROJECT_ID
    settings.FCM_SERVICE_ACCOUNT_JSON = "{not valid json"

    assert isinstance(build_transport(), NullPushTransport)


def test_missing_credential_file_degrades_to_null(settings: Any) -> None:
    settings.FCM_PROJECT_ID = PROJECT_ID
    settings.FCM_SERVICE_ACCOUNT_JSON = ""
    settings.FCM_SERVICE_ACCOUNT_FILE = "/nonexistent/service-account.json"

    assert isinstance(build_transport(), NullPushTransport)


# ── dispatch to a user's devices ─────────────────────────────────────────────


@pytest.mark.django_db
def test_notify_user_reaches_every_registered_device(user: Any) -> None:
    DeviceToken.objects.create(user=user, token="dev-a")
    DeviceToken.objects.create(user=user, token="dev-b")
    transport = RecordingTransport()
    push.set_transport(transport)

    delivered = notify_user(user, title="Hi", body="There", data={"task_id": "1"})

    assert delivered == 2
    assert {message.token for message in transport.messages} == {"dev-a", "dev-b"}
    assert all(message.data == {"task_id": "1"} for message in transport.messages)


@pytest.mark.django_db
def test_notify_user_is_a_no_op_without_devices(user: Any) -> None:
    transport = RecordingTransport()
    push.set_transport(transport)

    assert notify_user(user, title="Hi", body="There") == 0
    assert transport.batches == []


# ── task-status dispatch ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_awaiting_approval_pushes_a_review_request(task: AgentTask) -> None:
    DeviceToken.objects.create(user=task.user, token="dev")
    transport = RecordingTransport()
    push.set_transport(transport)

    delivered = notify_task_status(task.pk, AgentTask.Status.AWAITING_APPROVAL)

    assert delivered == 1
    message = transport.messages[0]
    assert message.title == "Changes ready to review"
    assert task.title in message.body
    assert message.data["status"] == "awaiting_approval"
    assert message.data["task_id"] == str(task.pk)
    assert message.data["type"] == "task_status"


@pytest.mark.django_db
def test_failed_copy_uses_only_the_first_error_line(task: AgentTask) -> None:
    task.error_message = "verification failed\nstack trace noise"
    task.save(update_fields=["error_message"])
    DeviceToken.objects.create(user=task.user, token="dev")
    transport = RecordingTransport()
    push.set_transport(transport)

    notify_task_status(task.pk, AgentTask.Status.FAILED)

    message = transport.messages[0]
    assert message.title == "Task failed"
    assert "verification failed" in message.body
    assert "stack trace noise" not in message.body


@pytest.mark.django_db
def test_in_flight_statuses_do_not_notify(task: AgentTask) -> None:
    DeviceToken.objects.create(user=task.user, token="dev")
    transport = RecordingTransport()
    push.set_transport(transport)

    assert notify_task_status(task.pk, AgentTask.Status.RUNNING) == 0
    assert transport.batches == []


@pytest.mark.django_db
def test_unknown_task_is_a_no_op() -> None:
    assert notify_task_status(987654, AgentTask.Status.FAILED) == 0


@pytest.mark.django_db
def test_a_broken_transport_never_raises(task: AgentTask) -> None:
    DeviceToken.objects.create(user=task.user, token="dev")
    push.set_transport(RecordingTransport(fail=True))

    assert notify_task_status(task.pk, AgentTask.Status.FAILED) == 0


# ── integration with the status funnel ───────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_terminal_status_change_dispatches_a_push() -> None:
    user = User.objects.create_user(username="pusher")
    repository = Repository.objects.create(
        user=user, github_id=1, name="r", full_name="pusher/r", default_branch="main"
    )
    task = AgentTask.objects.create(
        user=user,
        repository=repository,
        prompt="do it",
        feature_branch="codepilot/do-it",
        status=AgentTask.Status.RUNNING,
    )
    DeviceToken.objects.create(user=user, token="dev")
    transport = RecordingTransport()
    push.set_transport(transport)

    events.set_status(task, AgentTask.Status.FAILED, error_code="boom", error_message="It broke.")

    assert [message.data["status"] for message in transport.messages] == ["failed"]


@pytest.mark.django_db(transaction=True)
def test_cancellation_does_not_dispatch_a_push() -> None:
    user = User.objects.create_user(username="canceller")
    repository = Repository.objects.create(
        user=user, github_id=2, name="r", full_name="canceller/r", default_branch="main"
    )
    task = AgentTask.objects.create(
        user=user,
        repository=repository,
        prompt="do it",
        feature_branch="codepilot/do-it",
        status=AgentTask.Status.RUNNING,
    )
    DeviceToken.objects.create(user=user, token="dev")
    transport = RecordingTransport()
    push.set_transport(transport)

    events.set_status(task, AgentTask.Status.CANCELLED, error_code="cancelled")

    assert transport.batches == []
