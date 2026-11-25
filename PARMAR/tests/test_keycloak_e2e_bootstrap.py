import io
import http.client
import json
import sqlite3
import ssl
import stat
import subprocess
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from uuid import uuid4

import pytest

from PARMAR.identity_contracts import (
    AccountIdentity,
    AuthenticationContext,
    ProviderIdentity,
    VerifiedProviderIdentity,
    resolve_authenticated_principal,
)
from PARMAR.identity_store import SQLiteIdentityRepository
from PARMAR.tests import keycloak_e2e_bootstrap as bootstrap


def run_environment(tmp_path, run_id="73109", attempt="1"):
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(mode=0o700, parents=True)
    slug = f"parmar-keycloak-e2e-{run_id}-{attempt}"
    temp_dir = runner_temp / slug
    temp_dir.mkdir(mode=0o700)
    owner = f"{run_id}:{attempt}"
    (temp_dir / bootstrap.OWNER_MARKER).write_text(owner + "\n", encoding="utf-8")
    (temp_dir / bootstrap.OWNER_MARKER).chmod(0o600)
    github_env = tmp_path / "github-env"
    github_env.touch(mode=0o600)
    return {
        "RUNNER_TEMP": str(runner_temp),
        "PARMAR_E2E_RUN_ID": run_id,
        "PARMAR_E2E_RUN_ATTEMPT": attempt,
        "PARMAR_E2E_TMPDIR": str(temp_dir),
        "PARMAR_E2E_CONTAINER": f"{slug}-keycloak",
        "PARMAR_E2E_NETWORK": f"{slug}-net",
        "GITHUB_ENV": str(github_env),
    }


def prepared_run(tmp_path):
    environ = run_environment(tmp_path)
    paths = bootstrap.prepare_run(environ)
    context = bootstrap._load_context(environ)
    return environ, paths, context


def test_secret_free_realm_template_requires_exact_confidential_pkce_client():
    template = bootstrap._read_template()
    realm = template["realm"]
    client = template["client"]
    serialized = json.dumps(template)

    assert realm["realm"] == "parmar-e2e"
    assert realm["sslRequired"] == "all"
    assert realm["registrationAllowed"] is False
    assert realm["resetPasswordAllowed"] is False
    assert realm["bruteForceProtected"] is True
    assert client["publicClient"] is False
    assert client["clientAuthenticatorType"] == "client-secret"
    assert client["standardFlowEnabled"] is True
    assert client["implicitFlowEnabled"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["redirectUris"] == [bootstrap.REDIRECT_URI]
    assert client["webOrigins"] == [bootstrap.WEB_ORIGIN]
    assert "*" not in serialized
    assert "secret" not in client
    assert "credentials" not in template["test_user"]
    assert template["unbound_test_user"]["username"] == bootstrap.UNBOUND_TEST_USERNAME
    assert "credentials" not in template["unbound_test_user"]
    assert bootstrap.REDIRECT_URI == (
        "https://127.0.0.1:8080/api/auth/oidc/callback"
    )


def test_prepare_generates_private_credentials_and_four_exact_database_paths(tmp_path):
    environ = run_environment(tmp_path)
    output = io.StringIO()
    with redirect_stdout(output):
        paths = bootstrap.prepare_run(environ)

    context = bootstrap._load_context(environ)
    secrets_path = context.temp_dir / bootstrap.SECRETS_FILENAME
    runtime_secrets = bootstrap._load_secrets(context)
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(context.temp_dir.stat().st_mode) == 0o700
    assert set(paths) == set(bootstrap.DATABASE_FILENAMES)
    assert len({path.resolve() for path in paths.values()}) == 4
    for path in paths.values():
        assert path.parent == context.temp_dir
        assert path.is_file()
        assert not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for secret in runtime_secrets.values():
        assert secret not in output.getvalue()
        assert secret not in Path(environ["GITHUB_ENV"]).read_text(encoding="utf-8")
    github_env = Path(environ["GITHUB_ENV"]).read_text(encoding="utf-8")
    for environment_name, database_path in paths.items():
        assert f"{environment_name}={database_path}\n" in github_env
    assert f"PARMAR_E2E_SECRET_FILE={secrets_path}\n" in github_env
    assert not any(
        name in github_env
        for name in (
            "PARMAR_OIDC_CLIENT_SECRET=",
            "PARMAR_SESSION_STORE_KEY=",
            "PARMAR_APPROVAL_STORE_KEY=",
            "PARMAR_E2E_TEST_USER_PASSWORD=",
            "PARMAR_E2E_UNBOUND_TEST_USER_PASSWORD=",
        )
    )


def test_prepare_refuses_database_symlinks_before_opening_them(tmp_path):
    environ = run_environment(tmp_path)
    context = bootstrap._load_context(environ)
    paths = bootstrap._expected_database_paths(context)
    outside_path = tmp_path / "outside.sqlite3"
    paths["PARMAR_IDENTITY_DB_PATH"].symlink_to(outside_path)

    with pytest.raises(bootstrap.E2EBootstrapError, match="not a regular owned file"):
        bootstrap.prepare_run(environ)

    assert not outside_path.exists()


def test_prepare_is_repeatable_without_rotating_run_credentials(tmp_path):
    environ = run_environment(tmp_path)
    first_paths = bootstrap.prepare_run(environ)
    first_secrets = bootstrap._load_secrets(bootstrap._load_context(environ))

    second_paths = bootstrap.prepare_run(environ)
    second_secrets = bootstrap._load_secrets(bootstrap._load_context(environ))

    assert second_paths == first_paths
    assert second_secrets == first_secrets


def test_prepare_accepts_only_completed_matching_identity_fixture(tmp_path):
    environ, paths, context = prepared_run(tmp_path)
    subject = str(uuid4())
    repository = SQLiteIdentityRepository(paths["PARMAR_IDENTITY_DB_PATH"])
    bootstrap._seed_prebound_identity(
        repository,
        issuer=bootstrap.ISSUER,
        subject=subject,
    )
    bootstrap._write_private_json(
        context.temp_dir / bootstrap.RESULT_FILENAME,
        {
            "issuer": bootstrap.ISSUER,
            "account_id": bootstrap.TEST_ACCOUNT_ID,
            "subject": subject,
        },
    )

    assert bootstrap.prepare_run(environ) == paths

    (context.temp_dir / bootstrap.RESULT_FILENAME).write_text(
        json.dumps(
            {
                "issuer": bootstrap.ISSUER,
                "account_id": bootstrap.TEST_ACCOUNT_ID,
                "subject": "different-subject",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(bootstrap.E2EBootstrapError, match="fixture does not match"):
        bootstrap.prepare_run(environ)


def test_seeded_provider_identity_resolves_only_to_deterministic_test_account(tmp_path):
    environ, paths, context = prepared_run(tmp_path)
    del environ, context
    repository = SQLiteIdentityRepository(paths["PARMAR_IDENTITY_DB_PATH"])
    subject = str(uuid4())

    account = bootstrap._seed_prebound_identity(
        repository,
        issuer=bootstrap.ISSUER,
        subject=subject,
    )

    assert account == AccountIdentity.parse(bootstrap.TEST_ACCOUNT_ID)
    assert repository.find_existing_account(
        ProviderIdentity(bootstrap.ISSUER, subject)
    ) == account
    assert repository.find_existing_account(
        ProviderIdentity(bootstrap.ISSUER, f"unknown-{uuid4()}")
    ) is None
    with sqlite3.connect(paths["PARMAR_IDENTITY_DB_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_account_bindings"
        ).fetchone()[0] == 1


def test_unknown_subject_does_not_create_or_resolve_an_account(tmp_path):
    environ, paths, context = prepared_run(tmp_path)
    del environ, context
    repository = SQLiteIdentityRepository(paths["PARMAR_IDENTITY_DB_PATH"])
    now = datetime.now(timezone.utc)
    unknown_identity = ProviderIdentity(bootstrap.ISSUER, "unbound-keycloak-subject")

    class Adapter:
        def verify(self, assertion):
            del assertion
            return VerifiedProviderIdentity(
                unknown_identity,
                AuthenticationContext(now, now + timedelta(minutes=5)),
            )

    assert resolve_authenticated_principal(Adapter(), repository, "assertion") is None
    with sqlite3.connect(paths["PARMAR_IDENTITY_DB_PATH"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_duplicate_binding_and_conflicting_account_mapping_fail_closed(tmp_path):
    environ, paths, context = prepared_run(tmp_path)
    del environ, context
    repository = SQLiteIdentityRepository(paths["PARMAR_IDENTITY_DB_PATH"])
    provider = ProviderIdentity(bootstrap.ISSUER, str(uuid4()))
    account = bootstrap._seed_prebound_identity(
        repository,
        issuer=provider.issuer,
        subject=provider.subject,
    )
    with repository._connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO provider_account_bindings(issuer, subject, account_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    provider.issuer,
                    provider.subject,
                    account.serialize(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    other_environ, other_paths, _other_context = prepared_run(tmp_path / "other")
    del other_environ, _other_context
    other_repository = SQLiteIdentityRepository(other_paths["PARMAR_IDENTITY_DB_PATH"])
    other_account = other_repository._provision_account(AccountIdentity.generate())
    with other_repository._connection() as connection:
        connection.execute(
            """
            INSERT INTO provider_account_bindings(issuer, subject, account_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                provider.issuer,
                provider.subject,
                other_account.account_identity.serialize(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    with pytest.raises(bootstrap.E2EBootstrapError, match="conflicting provider binding"):
        bootstrap._seed_prebound_identity(
            other_repository,
            issuer=provider.issuer,
            subject=provider.subject,
        )
    assert other_repository.find_existing_account(provider) == other_account.account_identity


def test_different_runs_cannot_share_database_or_binding_state(tmp_path):
    first_environment = run_environment(tmp_path / "first", "73110", "1")
    second_environment = run_environment(tmp_path / "second", "73110", "2")
    first_paths = bootstrap.prepare_run(first_environment)
    second_paths = bootstrap.prepare_run(second_environment)
    first_repository = SQLiteIdentityRepository(first_paths["PARMAR_IDENTITY_DB_PATH"])
    second_repository = SQLiteIdentityRepository(second_paths["PARMAR_IDENTITY_DB_PATH"])
    subject = str(uuid4())

    bootstrap._seed_prebound_identity(
        first_repository,
        issuer=bootstrap.ISSUER,
        subject=subject,
    )

    assert first_paths["PARMAR_IDENTITY_DB_PATH"] != second_paths["PARMAR_IDENTITY_DB_PATH"]
    assert second_repository.find_existing_account(
        ProviderIdentity(bootstrap.ISSUER, subject)
    ) is None


def test_partial_bootstrap_failure_removes_only_owned_run_directory(tmp_path, monkeypatch):
    environ, paths, context = prepared_run(tmp_path)
    del paths
    secret_values = bootstrap._load_secrets(context)
    stderr = io.StringIO()

    def fail_bootstrap(environ):
        assert environ["PARMAR_E2E_RUN_ID"] == "73109"
        raise bootstrap.E2EBootstrapError(
            f"provider outage; must not log {secret_values['client_secret']}"
        )

    monkeypatch.setattr(bootstrap, "bootstrap_run", fail_bootstrap)
    with redirect_stderr(stderr):
        result = bootstrap.main(["bootstrap"], environ=environ)

    assert result == 1
    assert not context.temp_dir.exists()
    assert secret_values["client_secret"] not in stderr.getvalue()
    assert "provider outage" in stderr.getvalue()


def test_cleanup_refuses_a_directory_with_a_different_run_owner(tmp_path):
    environ = run_environment(tmp_path)
    context = bootstrap._load_context(environ)
    (context.temp_dir / bootstrap.OWNER_MARKER).write_text("different-run\n", encoding="utf-8")

    with pytest.raises(bootstrap.E2EBootstrapError, match="not owned"):
        bootstrap.cleanup_owned_temp_dir(context)
    assert context.temp_dir.exists()


def test_bootstrap_requires_exact_digest_and_loopback_only_container(tmp_path, monkeypatch):
    environ, paths, context = prepared_run(tmp_path)
    del environ, paths
    with pytest.raises(bootstrap.E2EBootstrapError, match="immutable Keycloak image digest"):
        bootstrap._verify_docker_resources(context, "")

    image = "quay.io/keycloak/keycloak@sha256:" + ("a" * 64)
    container = {
        "Config": {
            "Image": image,
            "Labels": {"parmar.e2e.owner": context.owner},
        },
        "State": {"Status": "running"},
        "HostConfig": {"NetworkMode": context.network_name},
        "NetworkSettings": {
            "Ports": {
                "8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8443"}],
                "8080/tcp": None,
            }
        },
    }
    network = {
        "Labels": {"parmar.e2e.owner": context.owner},
        "Internal": True,
    }

    def find_docker(name):
        assert name == "docker"
        return "/usr/bin/docker"

    monkeypatch.setattr(bootstrap.shutil, "which", find_docker)

    def inspect(arguments, **kwargs):
        assert kwargs == {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": 5,
        }
        resource = container if arguments[1] == "container" else network
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps([resource]),
            stderr="",
        )

    monkeypatch.setattr(bootstrap.subprocess, "run", inspect)
    bootstrap._verify_docker_resources(context, image)

    container["NetworkSettings"]["Ports"]["8443/tcp"] = [
        {"HostIp": "0.0.0.0", "HostPort": "8443"}
    ]
    with pytest.raises(bootstrap.E2EBootstrapError, match="exclusively on loopback"):
        bootstrap._verify_docker_resources(context, image)


def test_keycloak_mount_exposes_only_its_tls_certificate_and_key():
    script_path = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "scripts"
        / "parmar-keycloak-oidc-e2e-infra.sh"
    )
    script = script_path.read_text(encoding="utf-8")
    start = script.index("start_keycloak() {")
    end = script.index("\nwait_keycloak() {", start)
    start_function = script[start:end]

    assert 'KEYCLOAK_TLS_DIR="${RUN_TEMP_DIR}/keycloak-tls"' in script
    assert 'cp -- "$tls_dir/keycloak.crt" "$KEYCLOAK_TLS_DIR/keycloak.crt"' in script
    assert 'cp -- "$tls_dir/keycloak.key" "$KEYCLOAK_TLS_DIR/keycloak.key"' in script
    assert '--mount "type=bind,src=${keycloak_tls_dir},dst=/run/parmar-e2e/tls,readonly"' in start_function
    assert "src=${tls_dir}" not in start_function
    assert "ca.key" not in start_function
    assert "parmar.key" not in start_function
    assert '--group-add "$runner_gid"' in start_function
    assert 'test -r /run/parmar-e2e/tls/keycloak.crt' in start_function
    assert 'test -r /run/parmar-e2e/tls/keycloak.key' in start_function


@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://127.0.0.1:8443/admin/redirect-target",
        "https://example.invalid/admin/redirect-target",
    ],
    ids=("same-origin", "cross-origin"),
)
def test_authorized_admin_request_never_follows_redirect(tmp_path, redirect_url):
    client = bootstrap.KeycloakAdminClient(
        tmp_path / "unused-ca.pem",
        ssl_context=ssl.create_default_context(),
    )
    requests = []

    class SyntheticRedirectHandler(urllib.request.BaseHandler):
        handler_order = 100

        def https_open(self, request):
            requests.append(request)
            headers = Message()
            headers["Location"] = redirect_url

            class RedirectResponse(io.BytesIO):
                code = 302
                status = 302
                msg = "Found"

                def info(self):
                    return headers

                def geturl(self):
                    return request.full_url

            return RedirectResponse()

    client._opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        SyntheticRedirectHandler(),
        bootstrap._RejectRedirectHandler(),
    )
    with pytest.raises(bootstrap.E2EBootstrapError, match="redirects are not permitted"):
        client.request_json("GET", "/admin/realms", access_token="offline-bearer")

    assert len(requests) == 1
    assert requests[0].full_url == bootstrap.KEYCLOAK_ORIGIN + "/admin/realms"
    assert requests[0].get_header("Authorization") == "Bearer offline-bearer"


def test_safe_transport_ignores_proxy_environment_and_requires_verified_tls(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    created_proxy_handlers = []
    original_proxy_handler = urllib.request.ProxyHandler

    class TrackingProxyHandler(original_proxy_handler):
        def __init__(self, proxies=None):
            created_proxy_handlers.append(proxies)
            super().__init__(proxies)

    monkeypatch.setattr(urllib.request, "ProxyHandler", TrackingProxyHandler)
    client = bootstrap.KeycloakAdminClient(
        tmp_path / "unused-ca.pem",
        ssl_context=ssl.create_default_context(),
    )

    https_handler = next(
        handler
        for handler in client._opener.handlers
        if isinstance(handler, urllib.request.HTTPSHandler)
    )
    assert created_proxy_handlers == [{}]
    assert https_handler._context.verify_mode == ssl.CERT_REQUIRED
    assert https_handler._context.check_hostname is True
    assert any(
        isinstance(handler, bootstrap._RejectRedirectHandler)
        for handler in client._opener.handlers
    )

    with pytest.raises(bootstrap.E2EBootstrapError, match="verification is required"):
        bootstrap.KeycloakAdminClient(
            tmp_path / "unused-ca.pem",
            ssl_context=ssl._create_unverified_context(),
        )


def test_safe_transport_bounds_response_reads_and_timeout(tmp_path):
    client = bootstrap.KeycloakAdminClient(
        tmp_path / "unused-ca.pem",
        timeout_seconds=0.1,
        ssl_context=ssl.create_default_context(),
    )
    captured = {}

    class OversizedResponse:
        status = 200

        def read(self, size=-1):
            captured["read_size"] = size
            return b"x" * size

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            return False

    class FakeOpener:
        def open(self, request, *, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return OversizedResponse()

    client._opener = FakeOpener()
    with pytest.raises(bootstrap.E2EBootstrapError, match="oversized response"):
        client.request_json(
            "GET",
            "/admin/realms",
            access_token="offline-bearer",
        )

    assert captured["url"] == bootstrap.KEYCLOAK_ORIGIN + "/admin/realms"
    assert captured["authorization"] == "Bearer offline-bearer"
    assert captured["timeout"] <= 0.1
    assert captured["read_size"] == bootstrap.MAX_RESPONSE_BYTES + 1


def test_provider_outage_and_timeout_are_bounded_and_do_not_leak_response_body(
    tmp_path,
    monkeypatch,
):
    secret = "runtime-secret-that-must-not-be-logged"
    client = bootstrap.KeycloakAdminClient(
        tmp_path / "unused-ca.pem",
        timeout_seconds=0.1,
        ssl_context=ssl.create_default_context(),
    )

    def unavailable(request, *, timeout):
        assert request.full_url.startswith(bootstrap.KEYCLOAK_ORIGIN)
        assert timeout <= 0.1
        raise urllib.error.HTTPError(
            bootstrap.KEYCLOAK_ORIGIN,
            503,
            "unavailable",
            {},
            io.BytesIO(secret.encode()),
        )

    monkeypatch.setattr(client._opener, "open", unavailable)
    with pytest.raises(bootstrap.E2EBootstrapError, match="HTTP 503") as outage:
        client.request_json("GET", "/realms/master/.well-known/openid-configuration")
    assert secret not in str(outage.value)

    def timed_out(request, *, timeout):
        assert request.full_url.startswith(bootstrap.KEYCLOAK_ORIGIN)
        assert timeout <= 0.1
        raise TimeoutError

    timeout_client = bootstrap.KeycloakAdminClient(
        tmp_path / "unused-ca.pem",
        timeout_seconds=0.1,
        ssl_context=ssl.create_default_context(),
    )
    monkeypatch.setattr(timeout_client._opener, "open", timed_out)
    with pytest.raises(bootstrap.E2EBootstrapError, match="unavailable or timed out"):
        timeout_client.request_json("GET", "/realms/master/.well-known/openid-configuration")


@pytest.mark.parametrize(
    ("failure_point", "exception_factory"),
    [
        (
            "open",
            lambda payload: http.client.BadStatusLine(
                f"malformed status line: {payload}"
            ),
        ),
        (
            "open",
            lambda payload: http.client.RemoteDisconnected(
                f"disconnected with peer data: {payload}"
            ),
        ),
        (
            "read",
            lambda payload: http.client.IncompleteRead(
                payload.encode("utf-8"),
                len(payload.encode("utf-8")) + 20,
            ),
        ),
        (
            "read",
            lambda payload: http.client.BadStatusLine(
                f"malformed response read: {payload}"
            ),
        ),
        (
            "read",
            lambda payload: http.client.RemoteDisconnected(
                f"disconnected while reading: {payload}"
            ),
        ),
    ],
    ids=(
        "bad-status-line-on-open",
        "remote-disconnect-on-open",
        "incomplete-response-read",
        "bad-status-line-on-read",
        "remote-disconnect-on-read",
    ),
)
def test_http_protocol_errors_are_sanitized_on_open_and_read(
    tmp_path,
    monkeypatch,
    failure_point,
    exception_factory,
):
    environ, _, context = prepared_run(tmp_path)
    bearer = "synthetic-bearer-must-not-be-logged"
    runtime_secrets = bootstrap._load_secrets(context)
    sensitive_values = (
        bearer,
        "synthetic-response-fragment-must-not-be-logged",
        *runtime_secrets.values(),
    )
    payload = " ".join(sensitive_values)
    client = bootstrap.KeycloakAdminClient(
        tmp_path / "unused-ca.pem",
        ssl_context=ssl.create_default_context(),
    )

    class FailingResponse:
        status = 200

        def read(self, size=-1):
            del size
            raise exception_factory(payload)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            return False

    class FailingOpener:
        calls = 0

        def open(self, request, *, timeout):
            del timeout
            self.calls += 1
            assert request.get_header("Authorization") == f"Bearer {bearer}"
            if failure_point == "open":
                raise exception_factory(payload)
            return FailingResponse()

    opener = FailingOpener()
    client._opener = opener
    with pytest.raises(bootstrap.E2EBootstrapError) as raised:
        client.request_json(
            "GET",
            "/admin/realms",
            access_token=bearer,
        )
    assert opener.calls == 1
    assert all(value not in str(raised.value) for value in sensitive_values)
    assert (
        "unavailable or timed out"
        if failure_point == "open"
        else "response could not be read safely"
    ) in str(raised.value)

    opener.calls = 0

    def fail_bootstrap(settings):
        assert settings["PARMAR_E2E_RUN_ID"] == environ["PARMAR_E2E_RUN_ID"]
        client.request_json(
            "GET",
            "/admin/realms",
            access_token=bearer,
        )

    monkeypatch.setattr(bootstrap, "bootstrap_run", fail_bootstrap)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        result = bootstrap.main(["bootstrap"], environ=environ)

    assert result == 1
    assert opener.calls == 1
    assert all(value not in stderr.getvalue() for value in sensitive_values)
    assert "Keycloak Admin API" in stderr.getvalue()


@pytest.mark.parametrize(
    "wrapped",
    [False, True],
    ids=("direct-tls-error", "urlerror-wrapped"),
)
def test_tls_certificate_errors_are_sanitized_and_classified(
    tmp_path,
    monkeypatch,
    wrapped,
):
    environ, _, context = prepared_run(tmp_path)
    certificate_detail = "synthetic-certificate-detail-must-not-be-logged"
    bearer = "synthetic-tls-bearer-must-not-be-logged"
    runtime_secrets = bootstrap._load_secrets(context)
    sensitive_values = (
        certificate_detail,
        bearer,
        *runtime_secrets.values(),
    )
    failure = ssl.SSLCertVerificationError(
        f"peer certificate contains {certificate_detail} and {bearer}"
    )
    if wrapped:
        failure = urllib.error.URLError(failure)

    client = bootstrap.KeycloakAdminClient(
        tmp_path / "unused-ca.pem",
        ssl_context=ssl.create_default_context(),
    )

    class FailingOpener:
        calls = 0

        def open(self, request, *, timeout):
            del timeout
            self.calls += 1
            assert request.get_header("Authorization") == f"Bearer {bearer}"
            raise failure

    opener = FailingOpener()
    client._opener = opener

    def assert_sanitized_failure():
        with pytest.raises(
            bootstrap.E2EBootstrapError,
            match="TLS certificate verification failed",
        ) as raised:
            client.request_json(
                "GET",
                "/realms/master/.well-known/openid-configuration",
                access_token=bearer,
            )
        assert all(value not in str(raised.value) for value in sensitive_values)
        assert opener.calls == 1

    assert_sanitized_failure()

    opener.calls = 0

    def fail_bootstrap(settings):
        assert settings["PARMAR_E2E_RUN_ID"] == environ["PARMAR_E2E_RUN_ID"]
        client.request_json(
            "GET",
            "/realms/master/.well-known/openid-configuration",
            access_token=bearer,
        )

    monkeypatch.setattr(bootstrap, "bootstrap_run", fail_bootstrap)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        result = bootstrap.main(["bootstrap"], environ=environ)

    assert result == 1
    assert opener.calls == 1
    assert all(value not in stderr.getvalue() for value in sensitive_values)
    assert stderr.getvalue().count("TLS certificate verification failed") == 1


def test_discovery_timeout_fails_closed():
    class UnavailableClient:
        _deadline = 0.0

        def request_json(self, *args, **kwargs):
            assert not args
            assert not kwargs
            raise AssertionError("A zero-timeout discovery must not make a request.")

    with pytest.raises(bootstrap.E2EBootstrapError, match="did not become ready"):
        bootstrap._wait_for_discovery(
            UnavailableClient(),
            "/realms/master/.well-known/openid-configuration",
            expected_issuer=f"{bootstrap.KEYCLOAK_ORIGIN}/realms/master",
            realm_name="master",
            timeout_seconds=0,
        )
