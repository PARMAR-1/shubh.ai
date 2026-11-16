import io
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from PARMAR.identity import LocalDemoPrincipalResolver, ProviderBoundIdentityResolver
from PARMAR.identity_contracts import (
    AccountIdentity,
    AuthenticationContext,
    AuthenticatedPrincipal,
    ProviderIdentity,
    VerifiedProviderIdentity,
)
from PARMAR.tests.identity_test_support import trusted_session
from PARMAR.identity_store import SQLiteIdentityRepository
from PARMAR.interface import futuristic_app
from PARMAR.sessions import (
    InMemorySessionRepository,
    SESSION_COOKIE_NAME,
    SessionManager,
    SessionPolicy,
)


@pytest.fixture
def identity_api(monkeypatch, tmp_path):
    manager = SessionManager(
        InMemorySessionRepository(),
        SessionPolicy(idle_timeout_seconds=300, absolute_timeout_seconds=900),
    )
    repository = SQLiteIdentityRepository(tmp_path / "identity.sqlite3")
    monkeypatch.setattr(futuristic_app, "SESSION_MANAGER", manager)
    monkeypatch.setattr(futuristic_app, "IDENTITY_REPOSITORY", repository)
    monkeypatch.setattr(futuristic_app, "PRINCIPAL_RESOLVER", LocalDemoPrincipalResolver())
    return manager, repository


def credentials(manager, user_id=None):
    account_identity = (
        AccountIdentity(user_id) if user_id is not None else AccountIdentity.generate()
    )
    return trusted_session(manager, account_identity)


def test_unmapped_session_is_denied_on_account_owned_routes(identity_api):
    manager, repository = identity_api
    session = manager.create_authenticated_session(uuid4(), "legacy-unmapped")
    workspace_id = "00000000-0000-4000-8000-000000000001"
    profile_id = "00000000-0000-4000-8000-000000000002"
    requests = [
        ("GET", "/api/identity/profiles", None),
        ("GET", "/api/identity/workspaces", None),
        ("GET", f"/api/identity/workspaces/{workspace_id}/profiles", None),
        ("GET", "/api/conversations", None),
        ("GET", "/api/conversations/00000000-0000-4000-8000-000000000003", None),
        ("GET", "/api/memory", None),
        ("POST", "/api/session/profile", {"profile_id": profile_id}),
        ("POST", "/api/identity/profiles", {"name": "Untrusted"}),
        ("POST", "/api/identity/workspaces", {"name": "Untrusted"}),
        (
            "POST",
            f"/api/identity/workspaces/{workspace_id}/profiles/{profile_id}",
            {},
        ),
        ("POST", "/api/memory", {"action": "consent", "granted": True}),
        (
            "POST",
            "/api/approval",
            {"approval_id": "untrusted", "decision": "APPROVE"},
        ),
        (
            "POST",
            "/api/approval/cancel",
            {"approval_id": "untrusted", "reason_code": "USER_REQUEST"},
        ),
        ("POST", "/api/chat", {"message": "Help with project planning."}),
    ]

    for method, path, payload in requests:
        response, errors = request_for(
            manager,
            path,
            method=method,
            session=session,
            payload=payload,
        )
        if path in {"/api/approval", "/api/approval/cancel"}:
            assert response["status"] == 403
            assert errors == []
        else:
            assert response == {}
            expected_message = (
                "A trusted authenticated account session is required"
                if path == "/api/chat"
                else "An authenticated session is required"
            )
            assert errors == [(401, expected_message)]

    assert repository.list_profiles(session.session.user_id) == []


@pytest.mark.parametrize("field", ["email", "email_address", "emailaddress"])
def test_client_email_claims_cannot_set_account_identity(identity_api, field):
    manager, repository = identity_api
    session = credentials(manager)

    response, errors = request_for(
        manager,
        "/api/identity/profiles",
        method="POST",
        session=session,
        payload={"name": "Spoofed", field: "other@example.invalid"},
    )

    assert response == {}
    assert errors == [(400, "Client-supplied identity is not accepted")]
    assert repository.list_profiles(session.session.account_identity.value) == []


def request_for(manager, path, *, method, session=None, payload=None, extra_headers=None):
    body = json.dumps(payload if payload is not None else {}).encode("utf-8")
    response = {}
    errors = []
    headers = {"Content-Length": str(len(body))}
    if session is not None:
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={session.session_token}"
        csrf = manager.csrf_token(session.session.session_id)
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
    headers.update(extra_headers or {})
    request = SimpleNamespace(
        path=path,
        headers=headers,
        rfile=io.BytesIO(body),
        send_error=lambda status, message: errors.append((status, message)),
        _send_json=lambda status, result: response.update(status=status, result=result),
        _send_json_with_headers=lambda status, result, extra_headers: response.update(
            status=status,
            result=result,
            headers=extra_headers,
        ),
    )
    if method == "GET":
        futuristic_app.PARMARRequestHandler.do_GET(request)
    else:
        futuristic_app.PARMARRequestHandler.do_POST(request)
    return response, errors


def test_profile_workspace_api_requires_authenticated_session(identity_api):
    manager, _repository = identity_api

    for path in ("/api/identity/profiles", "/api/identity/workspaces"):
        response, errors = request_for(manager, path, method="GET")
        assert response == {}
        assert errors == [(401, "An authenticated session is required")]

        response, errors = request_for(manager, path, method="POST", payload={"name": "Private"})
        assert response == {}
        assert errors == [(401, "An authenticated session is required")]


def test_api_isolates_profiles_and_workspaces_between_users(identity_api):
    manager, repository = identity_api
    first_user = credentials(manager)
    second_user = credentials(manager)

    for session, path in (
        (first_user, "/api/identity/profiles"),
        (first_user, "/api/identity/workspaces"),
        (second_user, "/api/identity/profiles"),
    ):
        response, errors = request_for(
            manager,
            path,
            method="POST",
            session=session,
            payload={"name": "Blocked"},
        )
        assert response == {}
        assert errors == [(
            503,
            "Protected operation 'profile_workspace_write' is disabled: "
            "an atomic authorization fence is unavailable.",
        )]
    assert repository.list_profiles(first_user.session.user_id) == []
    assert repository.list_profiles(second_user.session.user_id) == []
    assert repository.list_workspaces(first_user.session.user_id) == []
    assert repository.list_workspaces(second_user.session.user_id) == []


def test_workspace_profile_sharing_is_runtime_no_go_before_repository_write(identity_api):
    manager, repository = identity_api
    owner_session = credentials(manager)
    profile = repository.create_profile(owner_session.session.user_id, "Locally seeded")
    workspace = repository.create_workspace(owner_session.session.user_id, "Locally seeded")

    response, errors = request_for(
        manager,
        f"/api/identity/workspaces/{workspace.workspace_id}/profiles/{profile.profile_id}",
        method="POST",
        session=owner_session,
    )

    assert response == {}
    assert errors == [(
        503,
        "Protected operation 'profile_workspace_write' is disabled: "
        "an atomic authorization fence is unavailable.",
    )]
    assert repository.list_workspace_profiles(
        owner_session.session.user_id,
        workspace.workspace_id,
    ) == []


def test_profile_create_rejects_client_supplied_owner(identity_api):
    manager, _repository = identity_api
    session = credentials(manager)

    response, errors = request_for(
        manager,
        "/api/identity/profiles",
        method="POST",
        session=session,
        payload={"name": "Forged", "owner_user_id": str(uuid4())},
    )

    assert response == {}
    assert errors == [(400, "Client-supplied identity is not accepted")]


@pytest.mark.parametrize("field", ["actor_id", "actor_user_id", "account_identity"])
def test_identity_api_rejects_client_supplied_canonical_actor(identity_api, field):
    manager, _repository = identity_api
    session = credentials(manager)

    response, errors = request_for(
        manager,
        "/api/identity/profiles",
        method="POST",
        session=session,
        payload={"name": "Forged", field: str(uuid4())},
    )

    assert response == {}
    assert errors == [(400, "Client-supplied identity is not accepted")]


def test_identity_api_rejects_actor_identity_query_and_header(identity_api):
    manager, _repository = identity_api

    query_response, query_errors = request_for(
        manager,
        f"/api/identity/profiles?actor_id={uuid4()}",
        method="GET",
    )
    header_response, header_errors = request_for(
        manager,
        "/api/identity/profiles",
        method="GET",
        extra_headers={"X-Actor-Id": str(uuid4())},
    )

    assert query_response == header_response == {}
    assert query_errors == header_errors == [
        (400, "Client-supplied identity is not accepted")
    ]


def test_trusted_identity_session_binds_http_principal_and_profile_owner(identity_api):
    manager, repository = identity_api
    first_account = AccountIdentity.generate()
    second_account = AccountIdentity.generate()
    providers = {
        "first-proof": ProviderIdentity("issuer", "first-subject"),
        "second-proof": ProviderIdentity("issuer", "second-subject"),
    }

    class Adapter:
        def verify(self, evidence):
            provider = providers.get(evidence)
            if provider is None:
                return None
            now = datetime.now(timezone.utc)
            return VerifiedProviderIdentity(
                provider,
                AuthenticationContext(now - timedelta(seconds=1), now + timedelta(minutes=5)),
            )

    class Bindings:
        def find_existing_account(self, provider):
            return {
                providers["first-proof"]: first_account,
                providers["second-proof"]: second_account,
            }.get(provider)

    resolver = ProviderBoundIdentityResolver(Adapter(), Bindings())
    first = manager.create_identity_session(resolver, "first-proof")
    second = manager.create_identity_session(resolver, "second-proof")
    assert first is not None and second is not None
    profile = repository.create_profile(first_account.value, "Canonical owner")
    assert manager.select_profile(
        first.session.session_id,
        first_account.value,
        profile.profile_id,
    ) is not None

    response, errors = request_for(
        manager,
        "/api/identity/profiles",
        method="GET",
        session=first,
    )
    assert errors == []
    assert [item["profile_id"] for item in response["result"]["profiles"]] == [
        str(profile.profile_id)
    ]

    response, errors = request_for(
        manager,
        "/api/identity/profiles",
        method="GET",
        session=second,
    )
    assert errors == []
    assert response["result"]["profiles"] == []

    request = SimpleNamespace(
        headers={"Cookie": f"{SESSION_COOKIE_NAME}={first.session_token}"},
        send_error=lambda status, message: pytest.fail(f"{status}: {message}"),
    )
    assert futuristic_app._resolve_request_context(request)
    assert request.request_context.authenticated_principal.account_identity == first_account
    assert request.request_context.principal.user_id == first_account.value


def test_authenticated_session_can_switch_owned_profiles_and_rejects_foreign_profile(identity_api):
    manager, repository = identity_api
    owner_session = credentials(manager)
    other_session = credentials(manager)
    first_profile = repository.create_profile(owner_session.session.user_id, "First")
    second_profile = repository.create_profile(owner_session.session.user_id, "Second")
    foreign_profile = repository.create_profile(other_session.session.user_id, "Foreign")

    for profile in (first_profile, second_profile, foreign_profile):
        response, errors = request_for(
            manager,
            "/api/session/profile",
            method="POST",
            session=owner_session,
            payload={"profile_id": str(profile.profile_id)},
        )
        assert response == {}
        assert errors == [(
            503,
            "Protected operation 'session_profile_selection' is disabled: "
            "an atomic authorization fence is unavailable.",
        )]
    selected = manager.resolve(owner_session.session_token)
    assert selected.active_profile_id is None


def test_home_session_and_profile_context_are_server_resolved_not_account_id_input(identity_api):
    manager, repository = identity_api
    session = credentials(manager)
    profile = repository.create_profile(session.session.user_id, "Home profile")
    selected = manager.select_profile(
        session.session.session_id,
        session.session.user_id,
        profile.profile_id,
    )
    assert selected is not None

    response, errors = request_for(manager, "/api/session", method="GET", session=session)

    assert errors == []
    assert response["result"]["authenticated"] is True
    assert response["result"]["active_profile_id"] == str(profile.profile_id)
    assert "account_id" not in response["result"]

    profiles, errors = request_for(
        manager,
        "/api/identity/profiles",
        method="GET",
        session=session,
    )
    assert errors == []
    assert [item["profile_id"] for item in profiles["result"]["profiles"]] == [
        str(profile.profile_id),
    ]

    forged, errors = request_for(
        manager,
        "/api/session?account_id=attacker",
        method="GET",
        session=session,
    )
    assert forged == {}
    assert errors == [(400, "Client-supplied identity is not accepted")]


def test_logout_clears_active_profile_from_server_session(identity_api):
    manager, repository = identity_api
    session = credentials(manager)
    profile = repository.create_profile(session.session.user_id, "Private")
    selected = manager.select_profile(
        session.session.session_id,
        session.session.user_id,
        profile.profile_id,
    )
    assert selected.active_profile_id == profile.profile_id

    response, errors = request_for(
        manager,
        "/api/logout",
        method="POST",
        session=session,
    )

    assert errors == []
    assert response["result"]["logged_out"] is True
    revoked = manager.repository.get_by_session_id(session.session.session_id)
    assert revoked.status == "REVOKED"
    assert revoked.active_profile_id is None
