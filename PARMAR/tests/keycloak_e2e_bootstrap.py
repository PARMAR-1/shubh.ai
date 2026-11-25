"""Disposable Keycloak bootstrap helpers used only by the GitHub E2E workflow."""

from __future__ import annotations

import argparse
import copy
import http.client
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

from PARMAR.identity_contracts import AccountIdentity, ProviderIdentity
from PARMAR.identity_store import AccountStatus, SQLiteIdentityRepository
from PARMAR.interface.approvals import SQLitePendingApprovalStore
from PARMAR.oidc import (
    OIDCConfiguration,
    oidc_state_store_from_environment,
)
from PARMAR.sessions import SQLiteSessionRepository


KEYCLOAK_ORIGIN = "https://127.0.0.1:8443"
REALM_NAME = "parmar-e2e"
ISSUER = f"{KEYCLOAK_ORIGIN}/realms/{REALM_NAME}"
CLIENT_ID = "parmar-e2e-client"
REDIRECT_URI = "https://127.0.0.1:8080/api/auth/oidc/callback"
WEB_ORIGIN = "https://127.0.0.1:8080"
TEST_USERNAME = "parmar-e2e-user"
UNBOUND_TEST_USERNAME = "parmar-e2e-unbound-user"
TEST_ACCOUNT_ID = "00000000-0000-4000-8000-000000000001"
BOOTSTRAP_ADMIN_USERNAME = "parmar-e2e-admin"
BOOTSTRAP_TIMEOUT_SECONDS = 90
REQUEST_TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 1_048_576
OWNER_MARKER = ".owner"
SECRETS_FILENAME = "bootstrap-secrets.json"
RESULT_FILENAME = "bootstrap-result.json"
DATABASE_FILENAMES = {
    "PARMAR_OIDC_STATE_DB_PATH": "oidc-state.sqlite3",
    "PARMAR_SESSION_DB_PATH": "sessions.sqlite3",
    "PARMAR_IDENTITY_DB_PATH": "identity.sqlite3",
    "PARMAR_APPROVAL_DB_PATH": "approvals.sqlite3",
}
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "e2e"
    / "keycloak"
    / "parmar-e2e-realm.json"
)


class E2EBootstrapError(RuntimeError):
    """Raised when disposable E2E configuration cannot be established safely."""


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class RunContext:
    run_id: str
    attempt: str
    owner: str
    slug: str
    runner_temp: Path
    temp_dir: Path
    container_name: str
    network_name: str


class KeycloakAdminClient:
    """Small bounded Admin REST client pinned to the run-local Keycloak origin."""

    def __init__(
        self,
        ca_certificate: Path,
        *,
        timeout_seconds: float = BOOTSTRAP_TIMEOUT_SECONDS,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise E2EBootstrapError("Keycloak bootstrap timeout must be positive.")
        if ssl_context is None:
            try:
                ssl_context = ssl.create_default_context(cafile=str(ca_certificate))
            except (OSError, ssl.SSLError):
                raise E2EBootstrapError("The ephemeral Keycloak CA is unavailable.") from None
        if (
            ssl_context.verify_mode != ssl.CERT_REQUIRED
            or not ssl_context.check_hostname
        ):
            raise E2EBootstrapError("Keycloak HTTPS certificate verification is required.")
        self._ssl_context = ssl_context
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl_context),
            _RejectRedirectHandler(),
        )
        self._deadline = time.monotonic() + timeout_seconds

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise E2EBootstrapError("Keycloak bootstrap exceeded its deadline.")
        return remaining

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        form: dict[str, str] | None = None,
        access_token: str | None = None,
        allowed_statuses: tuple[int, ...] = (200,),
    ) -> tuple[int, object | None]:
        if not path.startswith("/") or path.startswith("//"):
            raise E2EBootstrapError("Keycloak Admin API path is invalid.")
        if body is not None and form is not None:
            raise E2EBootstrapError("Keycloak request body is ambiguous.")
        origin = urllib.parse.urlsplit(KEYCLOAK_ORIGIN)
        if (
            origin.scheme != "https"
            or origin.hostname != "127.0.0.1"
            or origin.port != 8443
            or origin.username is not None
            or origin.password is not None
        ):
            raise E2EBootstrapError("Keycloak Admin API must use its loopback HTTPS endpoint.")

        headers = {"Accept": "application/json"}
        request_body: bytes | None = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            request_body = urllib.parse.urlencode(form).encode("ascii")
        elif body is not None:
            headers["Content-Type"] = "application/json"
            request_body = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"

        request = urllib.request.Request(
            KEYCLOAK_ORIGIN + path,
            data=request_body,
            headers=headers,
            method=method,
        )
        timeout = min(REQUEST_TIMEOUT_SECONDS, self._remaining())
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if 300 <= status < 400:
                raise E2EBootstrapError("Keycloak Admin API redirects are not permitted.") from None
            if status in allowed_statuses:
                return status, None
            raise E2EBootstrapError(
                f"Keycloak Admin API returned HTTP {status}."
            ) from None
        except ssl.SSLCertVerificationError:
            raise E2EBootstrapError(
                "Keycloak TLS certificate verification failed."
            ) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise E2EBootstrapError(
                    "Keycloak TLS certificate verification failed."
                ) from None
            raise E2EBootstrapError(
                "Keycloak Admin API is unavailable or timed out."
            ) from None
        except (
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ):
            raise E2EBootstrapError("Keycloak Admin API is unavailable or timed out.") from None

        try:
            with response:
                status = response.status
                if 300 <= status < 400:
                    raise E2EBootstrapError("Keycloak Admin API redirects are not permitted.")
                if status not in allowed_statuses:
                    raise E2EBootstrapError(
                        f"Keycloak Admin API returned HTTP {status}."
                    )
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except ssl.SSLCertVerificationError:
            raise E2EBootstrapError(
                "Keycloak TLS certificate verification failed."
            ) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise E2EBootstrapError(
                    "Keycloak TLS certificate verification failed."
                ) from None
            raise E2EBootstrapError(
                "Keycloak Admin API response could not be read safely."
            ) from None
        except (
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ):
            raise E2EBootstrapError("Keycloak Admin API response could not be read safely.") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise E2EBootstrapError("Keycloak returned an oversized response.")
        if not raw:
            return status, None
        try:
            return status, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise E2EBootstrapError("Keycloak returned invalid JSON.") from None


def _load_context(environ: Mapping[str, str]) -> RunContext:
    run_id = environ.get("PARMAR_E2E_RUN_ID", "")
    attempt = environ.get("PARMAR_E2E_RUN_ATTEMPT", "")
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", attempt):
        raise E2EBootstrapError("GitHub run ID and attempt must be positive integers.")
    runner_temp_value = environ.get("RUNNER_TEMP", "")
    if not runner_temp_value:
        raise E2EBootstrapError("RUNNER_TEMP is required; database defaults are disabled.")

    runner_temp_input = Path(runner_temp_value)
    if runner_temp_input.is_symlink() or not runner_temp_input.is_dir():
        raise E2EBootstrapError("RUNNER_TEMP must be a real existing directory.")
    runner_temp = runner_temp_input.resolve(strict=True)
    slug = f"parmar-keycloak-e2e-{run_id}-{attempt}"
    temp_dir = runner_temp / slug
    temp_dir_value = environ.get("PARMAR_E2E_TMPDIR", "")
    temp_dir_input = Path(temp_dir_value) if temp_dir_value else None
    if (
        temp_dir_input is None
        or temp_dir_input.is_symlink()
        or not temp_dir_input.is_dir()
        or temp_dir_input.resolve(strict=True) != temp_dir
    ):
        raise E2EBootstrapError("The run directory is not the expected run-owned temporary path.")
    if stat.S_IMODE(temp_dir.stat().st_mode) & 0o077:
        raise E2EBootstrapError("The run directory must not be accessible to group or other users.")

    owner = f"{run_id}:{attempt}"
    owner_path = temp_dir / OWNER_MARKER
    if owner_path.is_symlink() or not owner_path.is_file():
        raise E2EBootstrapError("The run directory ownership marker is unavailable.")
    if owner_path.read_text(encoding="utf-8").strip() != owner:
        raise E2EBootstrapError("The run directory ownership marker does not match this run.")

    container_name = f"{slug}-keycloak"
    network_name = f"{slug}-net"
    if environ.get("PARMAR_E2E_CONTAINER") != container_name:
        raise E2EBootstrapError("The Keycloak container name is not owned by this run.")
    if environ.get("PARMAR_E2E_NETWORK") != network_name:
        raise E2EBootstrapError("The Keycloak network name is not owned by this run.")

    return RunContext(
        run_id=run_id,
        attempt=attempt,
        owner=owner,
        slug=slug,
        runner_temp=runner_temp,
        temp_dir=temp_dir,
        container_name=container_name,
        network_name=network_name,
    )


def _load_secrets(context: RunContext) -> dict[str, str]:
    path = context.temp_dir / SECRETS_FILENAME
    if path.is_symlink() or not path.is_file():
        raise E2EBootstrapError("Runtime bootstrap credentials are unavailable.")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise E2EBootstrapError("Runtime bootstrap credentials have unsafe permissions.")
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise E2EBootstrapError("Runtime bootstrap credentials are invalid.") from None
    required = {
        "admin_password",
        "client_secret",
        "test_user_password",
        "unbound_test_user_password",
        "session_store_key",
        "approval_store_key",
    }
    if not isinstance(values, dict) or set(values) != required:
        raise E2EBootstrapError("Runtime bootstrap credentials have an invalid shape.")
    if any(not isinstance(values[key], str) or len(values[key]) < 32 for key in required):
        raise E2EBootstrapError("Runtime bootstrap credentials do not meet minimum length.")
    return values


def _write_private_json(path: Path, payload: dict[str, str]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=True, separators=(",", ":"))
            file.write("\n")
        os.chmod(path, 0o600)
    except FileExistsError:
        raise E2EBootstrapError("Runtime credentials already exist; refusing to overwrite them.") from None
    except OSError:
        raise E2EBootstrapError("Could not securely write runtime bootstrap credentials.") from None


def _read_template() -> dict[str, object]:
    try:
        template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise E2EBootstrapError("The secret-free Keycloak realm template is unavailable.") from None
    if not isinstance(template, dict):
        raise E2EBootstrapError("The Keycloak realm template has an invalid shape.")
    realm = template.get("realm")
    client = template.get("client")
    user = template.get("test_user")
    unbound_user = template.get("unbound_test_user")
    if not all(
        isinstance(item, dict)
        for item in (realm, client, user, unbound_user)
    ):
        raise E2EBootstrapError("The Keycloak realm template has an invalid shape.")
    if "secret" in client or "credentials" in user or "credentials" in unbound_user:
        raise E2EBootstrapError("The Keycloak template must not contain credentials.")
    client_attributes = client.get("attributes")
    redirects = client.get("redirectUris")
    origins = client.get("webOrigins")
    if (
        realm.get("realm") != REALM_NAME
        or realm.get("sslRequired") != "all"
        or realm.get("registrationAllowed") is not False
        or client.get("clientId") != CLIENT_ID
        or client.get("publicClient") is not False
        or client.get("standardFlowEnabled") is not True
        or client.get("implicitFlowEnabled") is not False
        or client.get("directAccessGrantsEnabled") is not False
        or not isinstance(client_attributes, dict)
        or client_attributes.get("pkce.code.challenge.method") != "S256"
        or redirects != [REDIRECT_URI]
        or origins != [WEB_ORIGIN]
        or user.get("username") != TEST_USERNAME
        or unbound_user.get("username") != UNBOUND_TEST_USERNAME
        or user.get("username") == unbound_user.get("username")
    ):
        raise E2EBootstrapError("The Keycloak template does not meet the approved security policy.")
    if any("*" in str(item) for item in redirects + origins):
        raise E2EBootstrapError("Wildcard Keycloak origins and redirects are not permitted.")
    return template


def _expected_database_paths(context: RunContext) -> dict[str, Path]:
    return {
        environment_name: context.temp_dir / filename
        for environment_name, filename in DATABASE_FILENAMES.items()
    }


def _append_github_env(path: Path, values: Mapping[str, str]) -> None:
    if not path.exists():
        raise E2EBootstrapError("GITHUB_ENV is unavailable.")
    if path.is_symlink() or not path.is_file():
        raise E2EBootstrapError("GITHUB_ENV is not a regular file.")
    try:
        with path.open("a", encoding="utf-8") as output:
            for name, value in values.items():
                if "\n" in value or "\r" in value:
                    raise E2EBootstrapError("Refusing a multiline GITHUB_ENV value.")
                output.write(f"{name}={value}\n")
    except OSError:
        raise E2EBootstrapError("Could not configure run-local application paths.") from None


def _initialize_run_databases(
    context: RunContext,
    runtime_secrets: Mapping[str, str],
    environ: Mapping[str, str],
) -> dict[str, Path]:
    paths = _expected_database_paths(context)
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in paths.values()):
        raise E2EBootstrapError("A run-local database path is not a regular owned file.")
    settings = dict(environ)
    for name, path in paths.items():
        configured_path = settings.get(name)
        if configured_path and Path(configured_path).expanduser().resolve() != path:
            raise E2EBootstrapError(f"{name} points outside the current run directory.")
        settings[name] = str(path)
    settings.update(
        {
            "PARMAR_SESSION_STORE_KEY": runtime_secrets["session_store_key"],
            "PARMAR_APPROVAL_STORE_KEY": runtime_secrets["approval_store_key"],
        }
    )

    configuration = OIDCConfiguration(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret=runtime_secrets["client_secret"],
        audience=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
    )
    state_store = oidc_state_store_from_environment(configuration, settings)
    session_repository = SQLiteSessionRepository.from_environment(settings)
    identity_repository = SQLiteIdentityRepository.from_environment(settings)
    approval_store = SQLitePendingApprovalStore.from_environment(settings)

    if state_store.database_path.resolve() != paths["PARMAR_OIDC_STATE_DB_PATH"]:
        raise E2EBootstrapError("The OIDC state store selected an unexpected database.")
    if session_repository.database_path.resolve() != paths["PARMAR_SESSION_DB_PATH"]:
        raise E2EBootstrapError("The session store selected an unexpected database.")
    if identity_repository.database_path.resolve() != paths["PARMAR_IDENTITY_DB_PATH"]:
        raise E2EBootstrapError("The identity store selected an unexpected database.")
    if approval_store.database_path.resolve() != paths["PARMAR_APPROVAL_DB_PATH"]:
        raise E2EBootstrapError("The approval store selected an unexpected database.")

    session_repository.get_by_token_hash("0" * 64)
    identity_repository.get_account(AccountIdentity.parse(TEST_ACCOUNT_ID))
    approval_store.inspect(
        "parmar-e2e-initialization-probe",
        user_id=uuid4(),
        session_id=uuid4(),
    )

    for path in paths.values():
        if path.is_symlink() or not path.is_file():
            raise E2EBootstrapError("A run-local database was not created as a regular file.")
        os.chmod(path, 0o600)
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise E2EBootstrapError("A run-local database has unsafe permissions.")
        try:
            with sqlite3.connect(path) as connection:
                integrity = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error:
            raise E2EBootstrapError("A run-local database failed integrity verification.") from None
        if integrity is None or integrity[0] != "ok":
            raise E2EBootstrapError("A run-local database failed integrity verification.")
    if len({path.resolve() for path in paths.values()}) != 4:
        raise E2EBootstrapError("E2E databases must use four distinct paths.")

    with sqlite3.connect(paths["PARMAR_IDENTITY_DB_PATH"]) as connection:
        accounts = connection.execute(
            "SELECT account_id, status FROM accounts ORDER BY account_id"
        ).fetchall()
        bindings = connection.execute(
            "SELECT issuer, subject, account_id FROM provider_account_bindings"
        ).fetchall()
    if accounts or bindings:
        if (
            len(accounts) != 1
            or accounts[0][0] != TEST_ACCOUNT_ID
            or accounts[0][1] != AccountStatus.ACTIVE.value
            or len(bindings) != 1
        ):
            raise E2EBootstrapError("The run-local identity database contains unexpected bindings.")

        result_path = context.temp_dir / RESULT_FILENAME
        if (
            result_path.is_symlink()
            or not result_path.is_file()
            or stat.S_IMODE(result_path.stat().st_mode) & 0o077
        ):
            raise E2EBootstrapError("The completed run-local identity fixture has no result record.")
        try:
            prior_result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise E2EBootstrapError("The completed run-local identity result is invalid.") from None
        if (
            not isinstance(prior_result, dict)
            or prior_result.get("issuer") != ISSUER
            or prior_result.get("account_id") != TEST_ACCOUNT_ID
            or prior_result.get("subject") != bindings[0][1]
            or bindings[0][0] != ISSUER
            or bindings[0][2] != TEST_ACCOUNT_ID
        ):
            raise E2EBootstrapError("The completed run-local identity fixture does not match.")

    if "GITHUB_ENV" in environ:
        app_environment = {
            **{name: str(path) for name, path in paths.items()},
            "PARMAR_E2E_SECRET_FILE": str(context.temp_dir / SECRETS_FILENAME),
            "PARMAR_ENVIRONMENT": "test",
            "PARMAR_OIDC_ENABLED": "true",
            "PARMAR_OIDC_ISSUER": ISSUER,
            "PARMAR_OIDC_CLIENT_ID": CLIENT_ID,
            "PARMAR_OIDC_AUDIENCE": CLIENT_ID,
            "PARMAR_OIDC_REDIRECT_URI": REDIRECT_URI,
            "PARMAR_OIDC_STATE_TTL_SECONDS": "300",
            "PARMAR_SESSION_IDLE_SECONDS": "600",
            "PARMAR_SESSION_ABSOLUTE_SECONDS": "1800",
            "PARMAR_UI_HOST": "127.0.0.1",
            "PARMAR_UI_ORIGIN": WEB_ORIGIN,
            "PARMAR_UI_TLS_CERTFILE": str(context.temp_dir / "tls" / "parmar.crt"),
            "PARMAR_UI_TLS_KEYFILE": str(context.temp_dir / "tls" / "parmar.key"),
        }
        _append_github_env(Path(environ["GITHUB_ENV"]), app_environment)
    return paths


def prepare_run(environ: Mapping[str, str] | None = None) -> dict[str, Path]:
    settings = os.environ if environ is None else environ
    context = _load_context(settings)
    _read_template()

    secret_path = context.temp_dir / SECRETS_FILENAME
    if secret_path.exists() or secret_path.is_symlink():
        runtime_secrets = _load_secrets(context)
    else:
        runtime_secrets = {
            "admin_password": secrets.token_urlsafe(48),
            "client_secret": secrets.token_urlsafe(48),
            "test_user_password": secrets.token_urlsafe(48),
            "unbound_test_user_password": secrets.token_urlsafe(48),
            "session_store_key": secrets.token_urlsafe(48),
            "approval_store_key": secrets.token_urlsafe(48),
        }
        _write_private_json(secret_path, runtime_secrets)

    paths = _initialize_run_databases(context, runtime_secrets, settings)
    return paths


def _verify_docker_resources(context: RunContext, image: str) -> None:
    if not re.fullmatch(r"quay\.io/keycloak/keycloak@sha256:[0-9a-f]{64}", image):
        raise E2EBootstrapError("A reviewed immutable Keycloak image digest is required.")
    docker = shutil.which("docker")
    if docker is None:
        raise E2EBootstrapError("Docker is unavailable for run-owned resource verification.")

    def inspect(arguments: list[str]) -> object:
        try:
            completed = subprocess.run(
                [docker, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise E2EBootstrapError("Could not inspect run-owned Docker resources.") from None
        if completed.returncode != 0:
            raise E2EBootstrapError("Could not inspect run-owned Docker resources.")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError:
            raise E2EBootstrapError("Docker returned invalid resource metadata.") from None

    container_rows = inspect(["container", "inspect", context.container_name])
    network_rows = inspect(["network", "inspect", context.network_name])
    if not isinstance(container_rows, list) or len(container_rows) != 1:
        raise E2EBootstrapError("The Keycloak container is not uniquely identified.")
    if not isinstance(network_rows, list) or len(network_rows) != 1:
        raise E2EBootstrapError("The Keycloak network is not uniquely identified.")
    container = container_rows[0]
    network = network_rows[0]
    if not isinstance(container, dict) or not isinstance(network, dict):
        raise E2EBootstrapError("Docker resource metadata is invalid.")
    labels = container.get("Config", {}).get("Labels", {})
    network_labels = network.get("Labels", {})
    if (
        not isinstance(labels, dict)
        or labels.get("parmar.e2e.owner") != context.owner
        or not isinstance(network_labels, dict)
        or network_labels.get("parmar.e2e.owner") != context.owner
    ):
        raise E2EBootstrapError("Docker resources are not owned by this workflow run.")
    if (
        container.get("Config", {}).get("Image") != image
        or container.get("State", {}).get("Status") != "running"
    ):
        raise E2EBootstrapError("The run-owned Keycloak container image or state is unexpected.")
    if network.get("Internal") is not True:
        raise E2EBootstrapError("The Keycloak network is not internally isolated.")
    host_config = container.get("HostConfig", {})
    if not isinstance(host_config, dict) or host_config.get("NetworkMode") != context.network_name:
        raise E2EBootstrapError("Keycloak is not attached to this run's isolated network.")
    port_bindings = container.get("NetworkSettings", {}).get("Ports", {})
    published_ports = (
        {name: bindings for name, bindings in port_bindings.items() if bindings}
        if isinstance(port_bindings, dict)
        else {}
    )
    if published_ports != {
        "8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8443"}]
    }:
        raise E2EBootstrapError("Keycloak is not published exclusively on loopback.")


def _validate_discovery(
    metadata: object,
    *,
    expected_issuer: str,
    realm_name: str,
) -> dict[str, str]:
    if not isinstance(metadata, dict) or metadata.get("issuer") != expected_issuer:
        raise E2EBootstrapError("Keycloak discovery issuer does not match the configured issuer.")
    endpoint_names = ("authorization_endpoint", "token_endpoint", "jwks_uri")
    endpoints: dict[str, str] = {}
    for name in endpoint_names:
        value = metadata.get(name)
        if not isinstance(value, str):
            raise E2EBootstrapError("Keycloak discovery metadata is incomplete.")
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 8443
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith(f"/realms/{realm_name}/")
        ):
            raise E2EBootstrapError("Keycloak discovery contains an untrusted endpoint.")
        endpoints[name] = value
    return endpoints


def _wait_for_discovery(
    client: KeycloakAdminClient,
    path: str,
    *,
    expected_issuer: str,
    realm_name: str,
    timeout_seconds: float,
) -> dict[str, str]:
    deadline = min(client._deadline, time.monotonic() + timeout_seconds)
    while time.monotonic() < deadline:
        try:
            status, metadata = client.request_json(
                "GET",
                path,
                allowed_statuses=(200, 404, 502, 503, 504),
            )
        except E2EBootstrapError:
            if time.monotonic() >= deadline:
                break
        else:
            if status == 200:
                return _validate_discovery(
                    metadata,
                    expected_issuer=expected_issuer,
                    realm_name=realm_name,
                )
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    raise E2EBootstrapError("Keycloak discovery did not become ready before its deadline.")


def _admin_access_token(
    client: KeycloakAdminClient,
    runtime_secrets: Mapping[str, str],
) -> str:
    status, payload = client.request_json(
        "POST",
        "/realms/master/protocol/openid-connect/token",
        form={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": BOOTSTRAP_ADMIN_USERNAME,
            "password": runtime_secrets["admin_password"],
        },
    )
    if status != 200 or not isinstance(payload, dict):
        raise E2EBootstrapError("Keycloak bootstrap administrator authentication failed.")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise E2EBootstrapError("Keycloak did not provide an administrator token.")
    return token


def _admin_request(
    client: KeycloakAdminClient,
    access_token: str,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    allowed_statuses: tuple[int, ...] = (200,),
) -> tuple[int, object | None]:
    return client.request_json(
        method,
        path,
        body=body,
        access_token=access_token,
        allowed_statuses=allowed_statuses,
    )


def _ensure_realm(
    client: KeycloakAdminClient,
    access_token: str,
    template_realm: Mapping[str, object],
    owner: str,
) -> None:
    realm = copy.deepcopy(dict(template_realm))
    attributes = realm.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    attributes["parmar.e2e.run-owner"] = owner
    realm["attributes"] = attributes
    path = f"/admin/realms/{REALM_NAME}"
    status, existing = _admin_request(
        client,
        access_token,
        "GET",
        path,
        allowed_statuses=(200, 404),
    )
    if status == 404:
        _admin_request(
            client,
            access_token,
            "POST",
            "/admin/realms",
            body=realm,
            allowed_statuses=(201, 409),
        )
        status, existing = _admin_request(
            client,
            access_token,
            "GET",
            path,
            allowed_statuses=(200,),
        )
    if status != 200 or not isinstance(existing, dict):
        raise E2EBootstrapError("The disposable Keycloak realm could not be verified.")
    existing_attributes = existing.get("attributes")
    required_values = {
        "registrationAllowed": False,
        "registrationEmailAsUsername": False,
        "resetPasswordAllowed": False,
        "rememberMe": False,
        "verifyEmail": False,
        "loginWithEmailAllowed": False,
        "duplicateEmailsAllowed": False,
        "bruteForceProtected": True,
    }
    if (
        existing.get("realm") != REALM_NAME
        or existing.get("enabled") is not True
        or existing.get("sslRequired") != "all"
        or any(existing.get(name) != value for name, value in required_values.items())
        or not isinstance(existing_attributes, dict)
        or existing_attributes.get("parmar.e2e.run-owner") != owner
    ):
        raise E2EBootstrapError("An existing Keycloak realm is not owned by this run or is unsafe.")


def _ensure_client(
    client: KeycloakAdminClient,
    access_token: str,
    template_client: Mapping[str, object],
    client_secret: str,
    owner: str,
) -> None:
    client_representation = copy.deepcopy(dict(template_client))
    client_representation["secret"] = client_secret
    attributes = client_representation.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    attributes["parmar.e2e.run-owner"] = owner
    client_representation["attributes"] = attributes
    query = urllib.parse.urlencode({"clientId": CLIENT_ID})
    path = f"/admin/realms/{REALM_NAME}/clients?{query}"
    status, rows = _admin_request(
        client,
        access_token,
        "GET",
        path,
        allowed_statuses=(200,),
    )
    if status != 200 or not isinstance(rows, list):
        raise E2EBootstrapError("The Keycloak client query failed.")
    if not rows:
        _admin_request(
            client,
            access_token,
            "POST",
            f"/admin/realms/{REALM_NAME}/clients",
            body=client_representation,
            allowed_statuses=(201, 409),
        )
        status, rows = _admin_request(
            client,
            access_token,
            "GET",
            path,
            allowed_statuses=(200,),
        )
    if status != 200 or not isinstance(rows, list) or len(rows) != 1:
        raise E2EBootstrapError("The confidential Keycloak client is not unique.")
    existing = rows[0]
    existing_attributes = existing.get("attributes") if isinstance(existing, dict) else None
    required_values = {
        "clientId": CLIENT_ID,
        "enabled": True,
        "protocol": "openid-connect",
        "clientAuthenticatorType": "client-secret",
        "publicClient": False,
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "authorizationServicesEnabled": False,
        "redirectUris": [REDIRECT_URI],
        "webOrigins": [WEB_ORIGIN],
    }
    if (
        not isinstance(existing, dict)
        or any(existing.get(name) != value for name, value in required_values.items())
        or not isinstance(existing_attributes, dict)
        or existing_attributes.get("pkce.code.challenge.method") != "S256"
        or existing_attributes.get("parmar.e2e.run-owner") != owner
    ):
        raise E2EBootstrapError("The existing Keycloak client is not owned by this run or is unsafe.")

    internal_id = existing.get("id")
    if not isinstance(internal_id, str) or not internal_id:
        raise E2EBootstrapError("Keycloak did not return a client identifier.")
    secret_path = (
        f"/admin/realms/{REALM_NAME}/clients/"
        f"{urllib.parse.quote(internal_id, safe='')}/client-secret"
    )
    _, credential = _admin_request(
        client,
        access_token,
        "GET",
        secret_path,
        allowed_statuses=(200,),
    )
    if (
        not isinstance(credential, dict)
        or not isinstance(credential.get("value"), str)
        or not hmac.compare_digest(credential["value"], client_secret)
    ):
        raise E2EBootstrapError("The Keycloak client secret does not match this run.")


def _find_test_user(
    client: KeycloakAdminClient,
    access_token: str,
    username: str,
) -> list[object]:
    query = urllib.parse.urlencode({"username": username, "exact": "true"})
    status, rows = _admin_request(
        client,
        access_token,
        "GET",
        f"/admin/realms/{REALM_NAME}/users?{query}",
        allowed_statuses=(200,),
    )
    if status != 200 or not isinstance(rows, list):
        raise E2EBootstrapError("The Keycloak test user query failed.")
    return rows


def _ensure_test_user(
    client: KeycloakAdminClient,
    access_token: str,
    template_user: Mapping[str, object],
    password: str,
    owner: str,
    *,
    username: str,
) -> str:
    rows = _find_test_user(client, access_token, username)
    user = copy.deepcopy(dict(template_user))
    user["attributes"] = {"parmar.e2e.run-owner": [owner]}
    user["credentials"] = [
        {"type": "password", "value": password, "temporary": False}
    ]
    if not rows:
        _admin_request(
            client,
            access_token,
            "POST",
            f"/admin/realms/{REALM_NAME}/users",
            body=user,
            allowed_statuses=(201, 409),
        )
        rows = _find_test_user(client, access_token, username)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise E2EBootstrapError("The Keycloak test user is not unique.")
    existing = rows[0]
    attributes = existing.get("attributes")
    user_id = existing.get("id")
    if (
        existing.get("username") != username
        or existing.get("enabled") is not True
        or not isinstance(attributes, dict)
        or attributes.get("parmar.e2e.run-owner") != [owner]
        or not isinstance(user_id, str)
    ):
        raise E2EBootstrapError("The existing Keycloak user is not owned by this run.")
    try:
        if str(UUID(user_id)) != user_id:
            raise ValueError
    except ValueError:
        raise E2EBootstrapError("Keycloak returned a noncanonical test subject.") from None

    # Reset only the password for the verified run-owned disposable user on retries.
    reset_path = (
        f"/admin/realms/{REALM_NAME}/users/"
        f"{urllib.parse.quote(user_id, safe='')}/reset-password"
    )
    _admin_request(
        client,
        access_token,
        "PUT",
        reset_path,
        body={"type": "password", "value": password, "temporary": False},
        allowed_statuses=(204,),
    )
    return user_id


def _seed_prebound_identity(
    repository: SQLiteIdentityRepository,
    *,
    issuer: str,
    subject: str,
) -> AccountIdentity:
    provider_identity = ProviderIdentity(issuer, subject)
    account_identity = AccountIdentity.parse(TEST_ACCOUNT_ID)
    record = repository.get_account(account_identity)
    if record is None:
        # This test-only path needs a deterministic account ID; production has no enrollment API.
        repository._provision_account(account_identity)
        record = repository.get_account(account_identity)
    if record is None or record.status is not AccountStatus.ACTIVE:
        raise E2EBootstrapError("The deterministic test account is unavailable or disabled.")

    try:
        with repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    "SELECT issuer, subject, account_id FROM provider_account_bindings "
                    "WHERE issuer = ? AND subject = ?",
                    (provider_identity.issuer, provider_identity.subject),
                ).fetchall()
                if rows:
                    if len(rows) != 1 or rows[0]["account_id"] != account_identity.serialize():
                        raise E2EBootstrapError(
                            "A conflicting provider binding already exists in the run database."
                        )
                else:
                    existing_account_bindings = connection.execute(
                        "SELECT issuer, subject FROM provider_account_bindings WHERE account_id = ?",
                        (account_identity.serialize(),),
                    ).fetchall()
                    if existing_account_bindings:
                        raise E2EBootstrapError(
                            "The deterministic test account has an unexpected provider binding."
                        )
                    connection.execute(
                        """
                        INSERT INTO provider_account_bindings(issuer, subject, account_id, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            provider_identity.issuer,
                            provider_identity.subject,
                            account_identity.serialize(),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                connection.execute("COMMIT")
            finally:
                if connection.in_transaction:
                    connection.rollback()
    except E2EBootstrapError:
        raise
    except sqlite3.IntegrityError:
        raise E2EBootstrapError("A duplicate or conflicting provider binding was rejected.") from None

    if repository.find_existing_account(provider_identity) != account_identity:
        raise E2EBootstrapError("The pre-bound test identity did not resolve to its account.")
    unknown = ProviderIdentity(issuer, f"unbound-{uuid4()}")
    if repository.find_existing_account(unknown) is not None:
        raise E2EBootstrapError("An unknown provider subject unexpectedly resolved.")
    return account_identity


def _verify_disposable_resources(context: RunContext, image: str) -> None:
    _verify_docker_resources(context, image)


def bootstrap_run(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    settings = os.environ if environ is None else environ
    context = _load_context(settings)
    _verify_disposable_resources(context, settings.get("PARMAR_KEYCLOAK_IMAGE", ""))
    runtime_secrets = _load_secrets(context)
    template = _read_template()
    ca_certificate = context.temp_dir / "tls" / "ca.crt"
    if ca_certificate.is_symlink() or not ca_certificate.is_file():
        raise E2EBootstrapError("The ephemeral Keycloak CA certificate is unavailable.")

    client = KeycloakAdminClient(ca_certificate)
    _wait_for_discovery(
        client,
        "/realms/master/.well-known/openid-configuration",
        expected_issuer=f"{KEYCLOAK_ORIGIN}/realms/master",
        realm_name="master",
        timeout_seconds=30,
    )
    access_token = _admin_access_token(client, runtime_secrets)
    _ensure_realm(client, access_token, template["realm"], context.owner)
    _ensure_client(
        client,
        access_token,
        template["client"],
        runtime_secrets["client_secret"],
        context.owner,
    )
    subject = _ensure_test_user(
        client,
        access_token,
        template["test_user"],
        runtime_secrets["test_user_password"],
        context.owner,
        username=TEST_USERNAME,
    )
    unbound_subject = _ensure_test_user(
        client,
        access_token,
        template["unbound_test_user"],
        runtime_secrets["unbound_test_user_password"],
        context.owner,
        username=UNBOUND_TEST_USERNAME,
    )
    if unbound_subject == subject:
        raise E2EBootstrapError("Keycloak returned the same subject for both test users.")
    endpoints = _wait_for_discovery(
        client,
        f"/realms/{REALM_NAME}/.well-known/openid-configuration",
        expected_issuer=ISSUER,
        realm_name=REALM_NAME,
        timeout_seconds=30,
    )

    identity_path = _expected_database_paths(context)["PARMAR_IDENTITY_DB_PATH"]
    repository = SQLiteIdentityRepository.from_environment(
        {**settings, "PARMAR_IDENTITY_DB_PATH": str(identity_path)}
    )
    account_identity = _seed_prebound_identity(
        repository,
        issuer=ISSUER,
        subject=subject,
    )
    if repository.find_existing_account(ProviderIdentity(ISSUER, unbound_subject)) is not None:
        raise E2EBootstrapError("The second Keycloak test identity is unexpectedly bound.")
    result = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "subject": subject,
        "unbound_subject": unbound_subject,
        "account_id": account_identity.serialize(),
        **endpoints,
    }
    result_path = context.temp_dir / RESULT_FILENAME
    if result_path.is_symlink():
        raise E2EBootstrapError("The bootstrap result path must not be a symbolic link.")
    try:
        with result_path.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            output.write("\n")
        os.chmod(result_path, 0o600)
    except FileExistsError:
        if not result_path.is_file() or stat.S_IMODE(result_path.stat().st_mode) & 0o077:
            raise E2EBootstrapError("A prior bootstrap result has unsafe permissions.")
        try:
            existing = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise E2EBootstrapError("A prior bootstrap result is invalid.") from None
        if existing != result:
            raise E2EBootstrapError("A prior bootstrap result conflicts with this run.")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raise E2EBootstrapError("Could not persist the non-secret bootstrap result.") from None
    return result


def cleanup_owned_temp_dir(context: RunContext) -> None:
    if not context.temp_dir.exists():
        return
    if (
        context.temp_dir.is_symlink()
        or context.temp_dir.resolve(strict=True) != context.runner_temp / context.slug
        or not (context.temp_dir / OWNER_MARKER).is_file()
        or (context.temp_dir / OWNER_MARKER).is_symlink()
        or (context.temp_dir / OWNER_MARKER).read_text(encoding="utf-8").strip() != context.owner
    ):
        raise E2EBootstrapError("Refusing to remove a directory not owned by this run.")
    shutil.rmtree(context.temp_dir)


def _admin_password(context: RunContext) -> str:
    password = _load_secrets(context)["admin_password"]
    if "\n" in password or "\r" in password:
        raise E2EBootstrapError("Runtime administrator credential is invalid.")
    return password


def _safe_error_message(error: Exception, context: RunContext | None) -> str:
    if not isinstance(error, E2EBootstrapError):
        return "A local bootstrap operation failed."
    message = str(error)
    if context is not None:
        try:
            runtime_secrets = _load_secrets(context)
        except (E2EBootstrapError, OSError):
            return "Keycloak bootstrap failed; credential-bearing diagnostics were suppressed."
        for secret in runtime_secrets.values():
            message = message.replace(secret, "[REDACTED]")
    return message


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "bootstrap", "admin-password"))
    arguments = parser.parse_args(argv)
    settings = os.environ if environ is None else environ
    context: RunContext | None = None
    try:
        context = _load_context(settings)
        if arguments.command == "prepare":
            prepare_run(settings)
            print("Prepared four run-local databases and runtime-only credentials.")
        elif arguments.command == "bootstrap":
            bootstrap_run(settings)
            print(
                "Disposable Keycloak realm, client, pre-bound identity, and unbound test identity verified."
            )
        else:
            sys.stdout.write(_admin_password(context))
        return 0
    except (E2EBootstrapError, OSError, sqlite3.Error, ValueError, UnicodeError) as error:
        message = _safe_error_message(error, context)
        cleanup_message = ""
        if context is not None:
            try:
                cleanup_owned_temp_dir(context)
            except (E2EBootstrapError, OSError):
                cleanup_message = " Run-owned temporary cleanup also failed."
        print(f"PARMAR Keycloak E2E bootstrap: {message}{cleanup_message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
