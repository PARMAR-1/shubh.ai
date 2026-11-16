import io
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from PARMAR.audit.decision_logs import DecisionLogManager
from PARMAR.chat.context import ChatContext
from PARMAR.chat.models import ChatResponse
from PARMAR.chat.readiness import ExternalAuthorization
from PARMAR.chat.router import ChatRouter
from PARMAR.chat.service import ChatService
from PARMAR.conversations import InMemoryConversationRepository
from PARMAR.identity import LocalDemoPrincipalResolver, ProviderBoundIdentityResolver
from PARMAR.identity_contracts import (
    AccountIdentity,
    AuthenticationContext,
    IdentityEvidenceState,
    ProviderIdentity,
    VerifiedProviderIdentity,
)
from PARMAR.identity_store import IdentityStorageUnavailable, SQLiteIdentityRepository
from PARMAR.interface import futuristic_app
from PARMAR.interface.approvals import (
    ApprovalStorageUnavailable,
    ApprovalState,
    PendingApprovalStore,
    approval_binding_for_pending,
)
from PARMAR.interface.dashboard import TerminalDashboard
from PARMAR.interface.ui_adapter import PARMARUIAdapter
from PARMAR.research.service import SearchService
from PARMAR.safety.enforcement_gate import CentralEnforcementGate
from PARMAR.sessions import (
    InMemorySessionRepository,
    SESSION_COOKIE_NAME,
    SessionManager,
    SessionPolicy,
    SessionStorageUnavailable,
)

_DEFAULT_SESSION = object()
_TEST_SESSION = None
_TEST_SESSION_MANAGER = None
_TEST_IDENTITY_REPOSITORY = None


from PARMAR.tests.identity_test_support import trusted_session


@pytest.fixture(autouse=True)
def isolate_audit_log(monkeypatch, tmp_path):
    global _TEST_SESSION, _TEST_SESSION_MANAGER, _TEST_IDENTITY_REPOSITORY
    original_init = DecisionLogManager.__init__

    def isolated_init(self, log_path=None):
        original_init(self, log_path or str(tmp_path / "approval-audit.jsonl"))

    monkeypatch.setattr(DecisionLogManager, "__init__", isolated_init)
    monkeypatch.setattr(futuristic_app, "PENDING_APPROVALS", PendingApprovalStore())
    manager = SessionManager(
        InMemorySessionRepository(),
        SessionPolicy(idle_timeout_seconds=600, absolute_timeout_seconds=1800),
    )
    _TEST_SESSION = trusted_session(manager)
    identity_repository = SQLiteIdentityRepository(tmp_path / "approval-identity.sqlite3")
    profile = identity_repository.create_profile(_TEST_SESSION.session.user_id, "Approval test")
    manager.select_profile(
        _TEST_SESSION.session.session_id,
        _TEST_SESSION.session.user_id,
        profile.profile_id,
    )
    _TEST_IDENTITY_REPOSITORY = identity_repository
    _TEST_SESSION_MANAGER = manager
    monkeypatch.setattr(futuristic_app, "SESSION_MANAGER", manager)
    monkeypatch.setattr(futuristic_app, "IDENTITY_REPOSITORY", identity_repository)
    monkeypatch.setattr(futuristic_app, "PRINCIPAL_RESOLVER", LocalDemoPrincipalResolver())


def post(path, payload, *, session=_DEFAULT_SESSION, headers=None):
    if session is _DEFAULT_SESSION:
        session = _TEST_SESSION
    body = json.dumps(payload).encode("utf-8")
    response = {}
    request_headers = {
        "Content-Length": str(len(body)),
        "Content-Type": "application/json",
    }
    if session is not None:
        request_headers["Cookie"] = f"{SESSION_COOKIE_NAME}={session.session_token}"
        csrf_token = _TEST_SESSION_MANAGER.csrf_token(session.session.session_id)
        if csrf_token is not None:
            request_headers["X-CSRF-Token"] = csrf_token
    request_headers.update(headers or {})
    request = SimpleNamespace(
        path=path,
        headers=request_headers,
        rfile=io.BytesIO(body),
        _send_json=lambda status, result: response.update(status=status, result=result),
        _send_json_with_headers=lambda status, result, extra: response.update(
            status=status, result=result, headers=extra,
        ),
        send_error=lambda status, message: response.update(status=status, result={"message": message}),
    )
    futuristic_app.PARMARRequestHandler.do_POST(request)
    return response


def assert_runtime_no_go(response, operation):
    assert response["status"] == 503
    assert response["result"]["message"] == (
        f"Protected operation '{operation}' is disabled: "
        "an atomic authorization fence is unavailable."
    )


def create_pending_review(request="Purchase a $500 software license"):
    response = post("/api/analyze", {"request": request})
    assert response["status"] == 200
    assert response["result"]["status"] == "APPROVAL_REQUIRED"
    assert response["result"].get("approval_id"), response["result"]
    return response["result"]


def test_untrusted_sessions_cannot_create_actionable_analysis_approvals():
    manager = _TEST_SESSION_MANAGER
    unmapped = manager.create_authenticated_session(uuid4(), "legacy-unmapped")
    expired = trusted_session(
        manager,
        now=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    revoked = trusted_session(manager)
    assert manager.revoke(revoked.session.session_id)

    unmapped_response = post(
        "/api/analyze",
        {"request": "Purchase a $500 software license"},
        session=unmapped,
    )
    assert unmapped_response["status"] == 200
    assert unmapped_response["result"]["status"] == "APPROVAL_REQUIRED"
    assert "approval_id" not in unmapped_response["result"]
    assert (
        unmapped_response["result"]["voki_contract"]["approval"]["record_available"]
        == "UNAVAILABLE"
    )

    for session in (expired, revoked):
        response = post(
            "/api/analyze",
            {"request": "Purchase a $500 software license"},
            session=session,
        )
        assert response["status"] == 200
        assert response["result"]["status"] == "APPROVAL_REQUIRED"
        assert "approval_id" not in response["result"]
        assert futuristic_app.PENDING_APPROVALS._pending == {}

    assert futuristic_app.PENDING_APPROVALS._pending == {}


def test_authenticated_session_uses_a_legitimately_selected_owned_profile():
    selected = _TEST_SESSION_MANAGER.resolve(_TEST_SESSION.session_token)
    assert selected is not None
    assert selected.active_profile_id is not None
    assert _TEST_IDENTITY_REPOSITORY.get_owned_profile(
        selected.user_id,
        selected.active_profile_id,
    ) is not None

    pending = _start_chat_approval(research=True)
    record = futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=selected.user_id,
        session_id=selected.session_id,
    )
    assert record is not None
    assert record.execution_context["profile_id"] == str(selected.active_profile_id)


def test_missing_active_profile_cannot_create_or_redeem_approval():
    session = trusted_session(_TEST_SESSION_MANAGER)

    creation = post("/api/analyze", {"request": "Purchase a $500 software license"}, session=session)
    assert creation["status"] == 409
    assert creation["result"]["message"] == "An active profile is required"

    pending = create_pending_review()
    selected = _TEST_SESSION_MANAGER.resolve(_TEST_SESSION.session_token)
    assert selected is not None
    _TEST_SESSION_MANAGER.repository._sessions[selected.session_id] = replace(
        selected,
        active_profile_id=None,
    )
    blocked = post(
        "/api/approval",
        {"approval_id": pending["approval_id"], "decision": "APPROVE"},
    )
    assert blocked["status"] == 409
    assert blocked["result"]["message"] == "An active profile is required"
    still_pending = futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert still_pending is not None


def test_deleted_active_profile_blocks_approval_without_consuming_record():
    pending = create_pending_review()
    selected = _TEST_SESSION_MANAGER.resolve(_TEST_SESSION.session_token)
    assert selected is not None and selected.active_profile_id is not None
    with sqlite3.connect(_TEST_IDENTITY_REPOSITORY.database_path) as connection:
        connection.execute(
            "DELETE FROM profiles WHERE profile_id = ?",
            (str(selected.active_profile_id),),
        )

    blocked = post(
        "/api/approval",
        {"approval_id": pending["approval_id"], "decision": "APPROVE"},
    )
    assert blocked["status"] == 409
    assert blocked["result"]["message"] == "An active profile is required"
    assert futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=selected.user_id,
        session_id=selected.session_id,
    ) is not None


def test_foreign_active_profile_cannot_create_chat_approval():
    foreign_user = uuid4()
    foreign_profile = _TEST_IDENTITY_REPOSITORY.create_profile(foreign_user, "Foreign")
    selected = _TEST_SESSION_MANAGER.select_profile(
        _TEST_SESSION.session.session_id,
        _TEST_SESSION.session.user_id,
        foreign_profile.profile_id,
    )
    assert selected is not None

    response = post("/api/chat", {"message": "Purchase a $500 software license"})
    assert response["status"] == 409
    assert response["result"]["message"] == "An active profile is required"
    assert futuristic_app.PENDING_APPROVALS._pending == {}


def test_unavailable_identity_storage_blocks_profile_dependent_approval(monkeypatch):
    class UnavailableIdentityRepository:
        def get_owned_profile(self, _user_id, _profile_id):
            raise IdentityStorageUnavailable("Identity storage is unavailable.")

    monkeypatch.setattr(futuristic_app, "IDENTITY_REPOSITORY", UnavailableIdentityRepository())

    response = post("/api/chat", {"message": "Purchase a $500 software license"})
    assert response["status"] == 503
    assert response["result"]["message"] == "Identity storage is unavailable"
    assert futuristic_app.PENDING_APPROVALS._pending == {}


def test_approval_is_bound_to_original_request_and_consumed_once():
    pending = create_pending_review()

    altered = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "request": "Purchase a $600 software license",
        "decision": "APPROVE",
    })
    assert altered["status"] == 409

    approved = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE",
    })
    assert approved["status"] == 200
    assert approved["result"]["status"] == "APPROVED"
    assert approved["result"]["enforcement"]["execution_allowed"] is True
    assert "approval_id" not in approved["result"]

    replay = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "REJECT",
    })
    assert replay["status"] == 410
    assert replay["result"]["status"] == "APPROVAL_INVALID"


def test_chat_approval_creates_the_same_kind_of_owned_server_side_pending_review(monkeypatch):
    calls = _install_counting_chat_provider(monkeypatch)
    before_conversation_count = len(
        futuristic_app.CONVERSATION_REPOSITORY.list_owned(_TEST_SESSION.session.user_id),
    )
    response = post("/api/chat", {"message": "Transfer $500 from the department budget to buy a laptop."})

    assert response["status"] == 503
    assert "atomic authorization fence is unavailable" in response["result"]["message"]
    assert calls == []
    assert futuristic_app.PENDING_APPROVALS._pending == {}
    assert len(
        futuristic_app.CONVERSATION_REPOSITORY.list_owned(_TEST_SESSION.session.user_id),
    ) == before_conversation_count


def test_approved_research_is_runtime_no_go_before_search_or_chat_dispatch(monkeypatch):
    search_calls = []
    chat_calls = []
    pending = _start_chat_approval()

    class UnexpectedService:
        def respond_approved(self, *_args, **_kwargs):
            chat_calls.append("dispatch")
            pytest.fail("Approved continuation reached ChatService despite runtime NO-GO")

    monkeypatch.setattr(futuristic_app, "ChatService", lambda **_kwargs: UnexpectedService())
    response = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(response, "approved_chat_continuation")
    assert search_calls == []
    assert chat_calls == []


def _install_counting_chat_provider(monkeypatch, *, content="The reviewed proposal can proceed.", error=None):
    calls = []

    class Provider:
        name = "approval-continuation-test"
        locality = "local"

        def generate(self, prompt, context=None):
            calls.append((prompt, context))
            if error is not None:
                raise error
            return ChatResponse(provider=self.name, content=content, safe=False)

    provider = Provider()

    def create_service(external_authorization=None):
        return ChatService(
            router=ChatRouter(provider=provider),
            external_authorization=external_authorization,
        )

    monkeypatch.setattr(futuristic_app, "ChatService", create_service)
    return calls


def _start_chat_approval(message="Purchase a $500 software license", *, research=False):
    review, dashboard, summary = PARMARUIAdapter.analyze_request_with_dashboard(message)
    selected = _TEST_SESSION_MANAGER.resolve(_TEST_SESSION.session_token)
    assert selected is not None and selected.active_profile_id is not None
    execution_context = {
        "context": ChatContext(),
        "selected_provider": None,
        "orchestration_mode": "SINGLE_PROVIDER",
        "recent_messages": None,
        "research": research,
        "conversation_id": str(uuid4()),
        "profile_id": str(selected.active_profile_id),
    }
    approval_id = futuristic_app.PENDING_APPROVALS.create(
        message,
        {"status": "APPROVAL_REQUIRED", "analysis": review},
        dashboard,
        summary,
        user_id=selected.user_id,
        session_id=selected.session_id,
        decision_id=summary["enforcement"]["decision_id"],
        approval_binding=approval_binding_for_pending(
            selected.user_id,
            summary["enforcement"]["decision_id"],
            execution_context,
        ),
        execution_context=execution_context,
    )
    return {"approval_id": approval_id, "request": message}


@pytest.mark.parametrize(
    "evidence_state",
    [IdentityEvidenceState.REVOKED, IdentityEvidenceState.EXPIRED],
)
def test_chat_does_not_execute_after_identity_evidence_loses_validity(
    monkeypatch,
    evidence_state,
):
    provider_calls = _install_counting_chat_provider(monkeypatch)
    service_factory = futuristic_app.ChatService

    def create_service(external_authorization=None):
        service = service_factory(external_authorization=external_authorization)

        class InvalidatingService:
            def respond(self, prompt, context=None, **options):
                if evidence_state is IdentityEvidenceState.EXPIRED:
                    assert _TEST_SESSION_MANAGER.resolve(
                        _TEST_SESSION.session_token,
                        now=_TEST_SESSION.session.authentication_context.expires_at
                        + timedelta(seconds=1),
                    ) is None
                else:
                    assert _TEST_SESSION_MANAGER.revoke_identity_evidence(
                        _TEST_SESSION.session.identity_evidence_id,
                    )
                return service.respond(prompt, context=context, **options)

        return InvalidatingService()

    monkeypatch.setattr(futuristic_app, "ChatService", create_service)
    response = post("/api/chat", {"message": "Plan a team lunch for next Friday."})

    assert_runtime_no_go(response, "authenticated_chat")
    assert provider_calls == []


def test_chat_execution_fails_closed_when_session_revalidation_storage_is_unavailable(
    monkeypatch,
):
    provider_calls = _install_counting_chat_provider(monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise SessionStorageUnavailable("session storage unavailable")

    monkeypatch.setattr(
        _TEST_SESSION_MANAGER,
        "resolve_trusted_session",
        unavailable,
    )
    response = post("/api/chat", {"message": "Plan a team lunch for next Friday."})

    assert response["status"] == 503
    assert provider_calls == []


def test_approved_chat_no_go_precedes_approval_execution_claim(monkeypatch):
    provider_calls = _install_counting_chat_provider(monkeypatch)
    pending_response = _start_chat_approval()
    pending = futuristic_app.PENDING_APPROVALS.inspect(
        pending_response["approval_id"],
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert pending is not None
    response = post("/api/approval", {
        "approval_id": pending_response["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(response, "approved_chat_continuation")
    assert provider_calls == []
    assert futuristic_app.PENDING_APPROVALS.inspect(
        pending_response["approval_id"],
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    ) is not None


def test_approved_chat_fails_closed_when_approval_state_cannot_be_checked(monkeypatch):
    provider_calls = _install_counting_chat_provider(monkeypatch)
    pending_response = _start_chat_approval()

    def unavailable(*_args, **_kwargs):
        raise ApprovalStorageUnavailable("approval storage unavailable")

    monkeypatch.setattr(futuristic_app.PENDING_APPROVALS, "inspect", unavailable)
    response = post("/api/approval", {
        "approval_id": pending_response["approval_id"],
        "decision": "APPROVE",
    })

    assert response["status"] == 503
    assert response["result"]["message"] == "Pending approval storage is unavailable"
    assert provider_calls == []


def test_identity_revocation_during_provider_call_withholds_later_release(monkeypatch):
    provider_calls = []

    class RevokingProvider:
        name = "revoking-provider-test"
        locality = "local"

        def generate(self, prompt, context=None):
            provider_calls.append(prompt)
            revocation = Thread(
                target=_TEST_SESSION_MANAGER.revoke_identity_evidence,
                args=(_TEST_SESSION.session.identity_evidence_id,),
            )
            revocation.start()
            revocation.join(timeout=2)
            assert not revocation.is_alive()
            assert _TEST_SESSION_MANAGER.identity_evidence_state(
                _TEST_SESSION.session.identity_evidence_id,
            ) is IdentityEvidenceState.REVOKED
            return ChatResponse(
                provider=self.name,
                content="This in-flight output must not be released.",
                safe=False,
            )

    monkeypatch.setattr(
        futuristic_app,
        "ChatService",
        lambda external_authorization=None: ChatService(
            router=ChatRouter(provider=RevokingProvider()),
            external_authorization=external_authorization,
        ),
    )
    response = post("/api/chat", {"message": "Plan a team lunch for next Friday."})

    assert_runtime_no_go(response, "authenticated_chat")
    assert provider_calls == []


def test_approved_chat_uses_original_review_calls_provider_once_and_releases_pass(monkeypatch):
    calls = _install_counting_chat_provider(monkeypatch)
    pending_result = _start_chat_approval()

    assert calls == []
    approved = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
        "request": "A client-supplied replacement request",
    })

    assert approved["status"] == 409
    assert calls == []

    approved = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(approved, "approved_chat_continuation")
    assert calls == []

    replay = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })
    assert_runtime_no_go(replay, "approved_chat_continuation")
    assert calls == []


def test_development_auth_browser_path_cannot_run_chat_or_approval(monkeypatch):
    monkeypatch.setenv("PARMAR_ENVIRONMENT", "development")
    monkeypatch.setenv("PARMAR_DEVELOPMENT_AUTH_ENABLED", "true")
    monkeypatch.setenv("PARMAR_UI_HOST", "127.0.0.1")
    monkeypatch.setenv("PARMAR_UI_TLS_CERTFILE", "/test/cert.pem")
    monkeypatch.setenv("PARMAR_UI_TLS_KEYFILE", "/test/key.pem")
    monkeypatch.setenv("PARMAR_UI_ORIGIN", "https://127.0.0.1:8080")
    monkeypatch.setattr(futuristic_app, "_request_is_tls", lambda _request: True)
    calls = _install_counting_chat_provider(monkeypatch)

    login = post(
        "/api/auth/development",
        {},
        session=None,
        headers={
            "Origin": "https://127.0.0.1:8080",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert login["status"] == 201
    assert login["result"] == {"authenticated": True}
    token = login["headers"]["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
    development_session = SimpleNamespace(
        session_token=token,
        session=_TEST_SESSION_MANAGER.resolve(token),
    )
    assert development_session.session.user_id == futuristic_app.DEVELOPMENT_AUTH_USER_ID

    analysis = post(
        "/api/analyze",
        {"request": "Purchase a $500 software license"},
        session=development_session,
    )
    assert analysis["status"] == 200
    assert analysis["result"]["status"] == "APPROVAL_REQUIRED"
    assert "approval_id" not in analysis["result"]
    assert (
        analysis["result"]["voki_contract"]["approval"]["record_available"]
        == "UNAVAILABLE"
    )

    pending = post(
        "/api/chat",
        {"message": "Purchase a $500 software license"},
        session=development_session,
    )
    assert pending["status"] == 401
    assert calls == []
    assert futuristic_app.PENDING_APPROVALS._pending == {}


@pytest.mark.parametrize("failure", ["invalid", "expired", "wrong_owner", "wrong_session"])
def test_invalid_approval_never_calls_chat_provider(monkeypatch, failure):
    calls = _install_counting_chat_provider(monkeypatch)
    pending_result = _start_chat_approval()
    payload = {"approval_id": pending_result["approval_id"], "decision": "APPROVE"}

    if failure == "invalid":
        payload["approval_id"] = "not-a-server-generated-token"
        response = post("/api/approval", payload)
    elif failure == "expired":
        token_hash = sha256(pending_result["approval_id"].encode("ascii")).hexdigest()
        pending = futuristic_app.PENDING_APPROVALS._pending[token_hash]
        futuristic_app.PENDING_APPROVALS._pending[token_hash] = replace(
            pending,
            expires_at=0,
        )
        response = post("/api/approval", payload)
    elif failure == "wrong_owner":
        other = trusted_session(_TEST_SESSION_MANAGER)
        response = post("/api/approval", payload, session=other)
    else:
        other = trusted_session(
            _TEST_SESSION_MANAGER,
            _TEST_SESSION.session.account_identity,
        )
        response = post("/api/approval", payload, session=other)

    assert response["status"] in {410, 403}
    assert calls == []


def test_approval_rejects_a_pending_record_whose_review_decision_binding_changed(monkeypatch):
    calls = _install_counting_chat_provider(monkeypatch)
    pending_result = _start_chat_approval()
    token_hash = sha256(pending_result["approval_id"].encode("ascii")).hexdigest()
    pending = futuristic_app.PENDING_APPROVALS._pending[token_hash]
    changed_response = deepcopy(pending.response)
    changed_response["analysis"]["enforcement"]["decision_id"] = "different-review"
    futuristic_app.PENDING_APPROVALS._pending[token_hash] = replace(
        pending,
        response=changed_response,
    )

    result = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })

    assert result["status"] == 410
    assert calls == []


def test_rejected_enforcement_after_approval_never_calls_chat_provider(monkeypatch):
    calls = _install_counting_chat_provider(monkeypatch)
    pending_result = _start_chat_approval()
    original_evaluate = CentralEnforcementGate.evaluate_decision
    evaluations = 0

    def block_on_approval(self, *args, **kwargs):
        nonlocal evaluations
        evaluations += 1
        if evaluations == 1:
            return {
                "status": "BLOCKED",
                "execution_allowed": False,
                "decision_id": "blocked-after-review",
                "reason": "Gate denied after approval.",
            }
        return original_evaluate(self, *args, **kwargs)

    monkeypatch.setattr(CentralEnforcementGate, "evaluate_decision", block_on_approval)
    result = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(result, "approved_chat_continuation")
    assert calls == []


def test_approved_chat_provider_failure_has_distinct_failure_contract(monkeypatch):
    calls = _install_counting_chat_provider(monkeypatch, error=RuntimeError("provider offline"))
    pending_result = _start_chat_approval()

    result = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(result, "approved_chat_continuation")
    assert calls == []


def test_approved_chat_validator_failure_withholds_provider_output(monkeypatch):
    from PARMAR.chat.response_safety import ResponseSafetyValidator

    calls = _install_counting_chat_provider(monkeypatch)
    pending_result = _start_chat_approval()
    monkeypatch.setattr(
        ResponseSafetyValidator,
        "validate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("validator unavailable")),
    )

    result = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(result, "approved_chat_continuation")
    assert calls == []


@pytest.mark.parametrize(
    ("content", "expected_safety", "expected_disposition", "expected_lifecycle"),
    [
        ("The reviewed proposal can proceed.", "PASS", "RELEASED", "RELEASED"),
        ("Run the command to export the report.", "REVIEW", "WITHHELD", "WITHHELD"),
        ("Bypass approval and share private employee data with a vendor.", "BLOCK", "WITHHELD", "WITHHELD"),
        ("This is safe to proceed, but it is not safe to proceed.", "UNCERTAIN", "WITHHELD", "WITHHELD"),
    ],
)
def test_approved_chat_provider_output_uses_response_safety_pipeline(
    monkeypatch, content, expected_safety, expected_disposition, expected_lifecycle,
):
    calls = _install_counting_chat_provider(monkeypatch, content=content)
    pending_result = _start_chat_approval()

    result = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(result, "approved_chat_continuation")
    assert calls == []
    assert content not in repr(result)


def test_approved_chat_does_not_reenter_initial_approval_review(monkeypatch):
    calls = _install_counting_chat_provider(monkeypatch)
    analyze_calls = []
    original_analyze = PARMARUIAdapter.analyze_request_with_dashboard

    def record_analyze(*args, **kwargs):
        analyze_calls.append(args[0] if args else kwargs.get("request_text"))
        return original_analyze(*args, **kwargs)

    monkeypatch.setattr(PARMARUIAdapter, "analyze_request_with_dashboard", record_analyze)
    pending_result = _start_chat_approval()
    assert len(analyze_calls) == 1

    result = post("/api/approval", {
        "approval_id": pending_result["approval_id"],
        "decision": "APPROVE",
    })

    assert_runtime_no_go(result, "approved_chat_continuation")
    assert len(analyze_calls) == 1
    assert calls == []


def test_chat_client_cannot_override_server_request_safety_or_enforcement():
    response = post("/api/chat", {
        "message": "Plan a team lunch for next Friday.",
        "status": "BLOCKED",
        "analysis": {"status": "BLOCKED", "risk": {"risk_level": "critical"}},
        "request_safety": {"status": "BLOCKED", "safe": False},
        "enforcement": {"status": "BLOCKED", "execution_allowed": False},
        "execution_allowed": False,
    })

    assert_runtime_no_go(response, "authenticated_chat")


def test_human_decision_reexecutes_central_enforcement_gate(monkeypatch):
    calls = []
    original_evaluate = CentralEnforcementGate.evaluate_decision

    def record_gate_call(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original_evaluate(self, *args, **kwargs)

    monkeypatch.setattr(CentralEnforcementGate, "evaluate_decision", record_gate_call)
    pending = create_pending_review()
    assert len(calls) == 1

    approved = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE",
    })

    assert approved["status"] == 200
    assert len(calls) == 2
    assert approved["result"]["voki_contract"]["approval"]["status"] == "APPROVED"
    assert approved["result"]["voki_contract"]["enforcement"]["status"] == "READY_FOR_ACTION"
    assert approved["result"]["voki_contract"]["provider"]["status"] == "NOT_STARTED"


def test_approval_for_one_review_cannot_be_used_for_another_request():
    pending = create_pending_review("Purchase a $500 software license")

    response = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "request": "Purchase a $5000 software license",
        "decision": "APPROVE",
    })

    assert response["status"] == 409
    assert response["result"]["status"] == "APPROVAL_REQUEST_MISMATCH"


def test_caller_supplied_altered_decision_cannot_replace_pending_review():
    pending = create_pending_review()

    response = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE",
        "request": pending["request"],
        "risk": {"risk_level": "low"},
        "decision_record": {"requires_human_approval": False},
        "privacy_result": {"allow_execution": True},
        "autonomy_result": {"allow_execution": True},
        "emergency_result": {"allow_execution": True},
    })

    assert response["status"] == 200
    assert response["result"]["risk"] == pending["risk"]
    assert response["result"]["decision"]["requires_human_approval"] is True
    assert response["result"]["decision"]["human_approval_status"] == "APPROVED"


def test_invalid_decision_does_not_consume_pending_approval():
    pending = create_pending_review()

    invalid = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE ANYWAY",
    })
    assert invalid["status"] == 400

    still_pending = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "REJECT",
    })
    assert still_pending["status"] == 200
    assert still_pending["result"]["status"] == "BLOCKED"


def test_missing_and_expired_approval_ids_are_denied():
    missing = post("/api/approval", {"decision": "APPROVE"})
    assert missing["status"] == 410
    assert missing["result"]["status"] == "APPROVAL_INVALID"

    store = PendingApprovalStore(ttl_seconds=1)
    token = store.create(
        "request",
        {},
        None,
        {},
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
        decision_id="request-decision",
        approval_binding=approval_binding_for_pending(
            _TEST_SESSION.session.user_id,
            "request-decision",
            None,
        ),
        now=0,
    )
    monkeypatch_store = futuristic_app.PENDING_APPROVALS
    expired_token = monkeypatch_store.create(
        "expired",
        {},
        None,
        {},
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
        decision_id="expired-decision",
        approval_binding=approval_binding_for_pending(
            _TEST_SESSION.session.user_id,
            "expired-decision",
            None,
        ),
        now=0,
    )
    assert store.consume(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
        now=2,
    )[1] == "EXPIRED"

    expired = post("/api/approval", {
        "approval_id": expired_token,
        "decision": "APPROVE",
    })
    assert expired["status"] == 410
    assert expired["result"]["status"] == "APPROVAL_INVALID"


def test_adapter_text_alone_cannot_authorize_a_decision():
    response = PARMARUIAdapter.process_human_decision(
        "Purchase a $500 software license",
        "APPROVE",
    )

    assert response["status"] == "APPROVAL_REQUIRED"
    assert response["approval_error"] == "PENDING_REVIEW_REQUIRED"
    assert response["enforcement"]["execution_allowed"] is False


def test_pending_approval_applies_to_stored_review_not_new_text():
    original_text = "Purchase a $500 software license"
    initial, dashboard, summary = PARMARUIAdapter.analyze_request_with_dashboard(original_text)
    pending = futuristic_app.PENDING_APPROVALS.create(
        original_text,
        initial,
        dashboard,
        summary,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
        decision_id=summary["enforcement"]["decision_id"],
        approval_binding=approval_binding_for_pending(
            _TEST_SESSION.session.user_id,
            summary["enforcement"]["decision_id"],
            None,
        ),
    )
    record = futuristic_app.PENDING_APPROVALS.inspect(
        pending,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert record is not None

    result = PARMARUIAdapter.apply_pending_human_decision(
        record.response.copy(),
        record.dashboard,
        record.summary,
        "REJECT",
    )

    assert result["request"] == original_text
    assert result["status"] == "BLOCKED"
    assert result["enforcement"]["execution_allowed"] is False


def test_approval_token_alone_and_unauthenticated_caller_cannot_redeem():
    pending = create_pending_review()
    payload = {"approval_id": pending["approval_id"], "decision": "APPROVE"}

    response = post("/api/approval", payload, session=None)

    assert response["status"] == 403
    still_pending = futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert still_pending is not None


def test_approval_rejects_wrong_user_and_wrong_session():
    pending = create_pending_review()
    other_user_session = trusted_session(_TEST_SESSION_MANAGER)
    same_user_other_session = trusted_session(
        _TEST_SESSION_MANAGER,
        _TEST_SESSION.session.account_identity,
    )
    payload = {"approval_id": pending["approval_id"], "decision": "APPROVE"}

    wrong_user = post("/api/approval", payload, session=other_user_session)
    wrong_session = post("/api/approval", payload, session=same_user_other_session)

    assert wrong_user["status"] == 410
    assert wrong_session["status"] == 410


def test_owner_can_cancel_pending_approval_idempotently_and_replay_stays_denied():
    pending = create_pending_review()
    token = pending["approval_id"]
    record = futuristic_app.PENDING_APPROVALS.inspect(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert record is not None

    cancelled = post("/api/approval/cancel", {
        "approval_id": token,
        "reason_code": "USER_REQUEST",
    })
    repeated = post("/api/approval/cancel", {
        "approval_id": token,
        "reason_code": "CONTEXT_CHANGED",
    })
    replay = post("/api/approval", {
        "approval_id": token,
        "decision": "APPROVE",
    })

    assert cancelled["status"] == 200
    assert cancelled["result"]["status"] == "CANCELLED"
    assert cancelled["result"]["idempotent"] is False
    assert repeated["status"] == 200
    assert repeated["result"]["idempotent"] is True
    assert replay["status"] == 410
    stored = futuristic_app.PENDING_APPROVALS._pending[sha256(token.encode("ascii")).hexdigest()]
    assert stored.state is ApprovalState.CANCELLED
    assert stored.audit_evidence["event"] == "APPROVAL_CANCELLED"
    assert stored.audit_evidence["reason_code"] == "USER_REQUEST"
    assert stored.audit_evidence["actor_user_id"] == str(_TEST_SESSION.session.user_id)
    assert stored.audit_evidence["session_id"] == str(_TEST_SESSION.session.session_id)
    assert stored.audit_evidence["timestamp"]


def test_unauthorized_cancellation_is_generic_and_leaves_approval_pending():
    pending = create_pending_review()
    other_session = trusted_session(_TEST_SESSION_MANAGER)
    same_user_other_session = trusted_session(
        _TEST_SESSION_MANAGER,
        _TEST_SESSION.session.account_identity,
    )
    payload = {
        "approval_id": pending["approval_id"],
        "reason_code": "USER_REQUEST",
    }

    wrong_user = post("/api/approval/cancel", payload, session=other_session)
    wrong_session = post("/api/approval/cancel", payload, session=same_user_other_session)

    assert wrong_user["status"] == wrong_session["status"] == 410
    assert wrong_user["result"] == wrong_session["result"]
    assert futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    ) is not None


def test_approval_owner_and_actor_use_the_same_trusted_canonical_identity():
    global _TEST_SESSION, _TEST_SESSION_MANAGER, _TEST_IDENTITY_REPOSITORY
    first_account = AccountIdentity.generate()
    second_account = AccountIdentity.generate()
    providers = {
        "first-proof": ProviderIdentity("issuer", "first"),
        "second-proof": ProviderIdentity("issuer", "second"),
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

    manager = SessionManager(
        InMemorySessionRepository(),
        SessionPolicy(idle_timeout_seconds=600, absolute_timeout_seconds=1800),
    )
    resolver = ProviderBoundIdentityResolver(Adapter(), Bindings())
    owner_session = manager.create_identity_session(resolver, "first-proof")
    other_session = manager.create_identity_session(resolver, "second-proof")
    assert owner_session is not None and other_session is not None
    identity_repository = _TEST_IDENTITY_REPOSITORY
    for session in (owner_session, other_session):
        profile = identity_repository.create_profile(session.session.user_id, "Canonical approval")
        assert manager.select_profile(
            session.session.session_id,
            session.session.user_id,
            profile.profile_id,
        ) is not None
    _TEST_SESSION = owner_session
    _TEST_SESSION_MANAGER = manager
    futuristic_app.SESSION_MANAGER = manager

    pending = create_pending_review()
    record = futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=first_account.value,
        session_id=owner_session.session.session_id,
    )
    assert record is not None
    assert record.user_id == first_account.value
    assert record.approval_binding.actor_id == first_account.value

    unauthorized_decision = post(
        "/api/approval",
        {"approval_id": pending["approval_id"], "decision": "APPROVE"},
        session=other_session,
    )
    unauthorized_cancel = post(
        "/api/approval/cancel",
        {"approval_id": pending["approval_id"], "reason_code": "USER_REQUEST"},
        session=other_session,
    )
    spoofed_actor = post(
        "/api/approval",
        {
            "approval_id": pending["approval_id"],
            "decision": "APPROVE",
            "actor_id": str(first_account.value),
        },
        session=other_session,
    )

    assert unauthorized_decision["status"] == 410
    assert unauthorized_cancel["status"] == 410
    assert spoofed_actor["status"] == 400
    assert futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=first_account.value,
        session_id=owner_session.session.session_id,
    ) is not None


def test_cancellation_rejects_binding_mismatch_and_expiry():
    pending = create_pending_review()
    token = pending["approval_id"]
    record = futuristic_app.PENDING_APPROVALS.inspect(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert record is not None and record.approval_binding is not None
    wrong_binding = replace(record.approval_binding, resource_id="different-resource")
    store = futuristic_app.PENDING_APPROVALS

    assert store.cancel(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
        reason_code="USER_REQUEST",
        approval_binding=wrong_binding,
    ) == "BINDING_MISMATCH"
    assert store.cancel(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
        reason_code="USER_REQUEST",
        approval_binding=record.approval_binding,
        now=record.expires_at + 1,
    ) == "EXPIRED"
    assert store.inspect(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    ) is None


def test_consumed_approval_cannot_be_cancelled():
    pending = create_pending_review()
    token = pending["approval_id"]
    record = futuristic_app.PENDING_APPROVALS.inspect(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert record is not None and record.approval_binding is not None
    assert futuristic_app.PENDING_APPROVALS.consume(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
        decision="APPROVE",
        approval_binding=record.approval_binding,
    )[1] == "OK"

    cancelled = post("/api/approval/cancel", {
        "approval_id": token,
        "reason_code": "USER_REQUEST",
    })

    assert cancelled["status"] == 410


def test_revoked_session_cannot_redeem_its_pending_approval():
    pending = create_pending_review()
    _TEST_SESSION_MANAGER.revoke(_TEST_SESSION.session.session_id)

    response = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE",
    })

    assert response["status"] == 403


def test_revoked_identity_evidence_cannot_redeem_its_pending_approval():
    pending = create_pending_review()
    authentication = _TEST_SESSION.session.authentication_context
    assert authentication is not None
    assert _TEST_SESSION_MANAGER.revoke_identity_evidence(authentication.evidence_id)

    response = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE",
    })

    assert response["status"] == 403


def test_expired_session_cannot_redeem_its_pending_approval():
    pending = create_pending_review()
    session = _TEST_SESSION_MANAGER.resolve(_TEST_SESSION.session_token)
    assert session is not None
    assert _TEST_SESSION_MANAGER.resolve(
        _TEST_SESSION.session_token,
        now=session.idle_expires_at,
    ) is None

    response = post("/api/approval", {
        "approval_id": pending["approval_id"],
        "decision": "APPROVE",
    })

    assert response["status"] == 403
    assert futuristic_app.PENDING_APPROVALS.inspect(
        pending["approval_id"],
        user_id=session.user_id,
        session_id=session.session_id,
    ) is not None


def test_unbound_legacy_approval_is_not_inspectable_or_consumable():
    pending = create_pending_review()
    token = pending["approval_id"]
    token_hash = sha256(token.encode("ascii")).hexdigest()
    stored = futuristic_app.PENDING_APPROVALS._pending[token_hash]
    futuristic_app.PENDING_APPROVALS._pending[token_hash] = replace(
        stored,
        user_id=None,
        session_id=None,
    )

    assert futuristic_app.PENDING_APPROVALS.inspect(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    ) is None
    consumed, state = futuristic_app.PENDING_APPROVALS.consume(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert consumed is None
    assert state == "OWNER_MISMATCH"


def test_concurrent_approval_consumption_succeeds_only_once():
    from concurrent.futures import ThreadPoolExecutor

    pending = create_pending_review()
    token = pending["approval_id"]
    record = futuristic_app.PENDING_APPROVALS.inspect(
        token,
        user_id=_TEST_SESSION.session.user_id,
        session_id=_TEST_SESSION.session.session_id,
    )
    assert record is not None
    assert record.approval_binding is not None

    def consume():
        return futuristic_app.PENDING_APPROVALS.consume(
            token,
            user_id=_TEST_SESSION.session.user_id,
            session_id=_TEST_SESSION.session.session_id,
            decision="APPROVE",
            approval_binding=record.approval_binding,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: consume(), range(8)))

    assert sum(record is not None for record, _state in results) == 1


def test_missing_safety_signal_never_authorizes_approval():
    valid = {"allow_execution": True}
    for missing_name in ("privacy_result", "autonomy_result", "emergency_result"):
        values = {
            "privacy_result": valid,
            "autonomy_result": valid,
            "emergency_result": valid,
        }
        values[missing_name] = None
        outcome = CentralEnforcementGate().evaluate_decision(
            {"risk": {"risk_level": "high"}, "requires_human_approval": True},
            human_approval="APPROVED",
            **values,
        )
        assert outcome["execution_allowed"] is False
        assert outcome["status"] == "BLOCKED"
