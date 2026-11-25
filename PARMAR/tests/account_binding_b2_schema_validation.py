"""Manual Gate 2 schema harness; not collected by the default pytest pattern.

An explicit pytest invocation extracts and executes the proposed B2 DDL in a
temporary synthetic database. Do not invoke the tests before the required
Gate 2 prototype authorization is recorded. Embedded-NUL length semantics are
an unresolved data-model decision and are deliberately not asserted here.
The synthetic `accounts` parent models only its primary key and foreign-key
behavior; it does not recreate or validate the current account-table triggers.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from PARMAR.identity_contracts import ProviderIdentity


_DDL_MARKER = (
    "**Illustrative SQLite DDL — unimplemented; do not execute as part of this design review**"
)


def _b2_schema_sql() -> str:
    design_path = Path(__file__).resolve().parents[1] / "ACCOUNT-BINDING-B2-SCHEMA-DESIGN.md"
    design = design_path.read_text(encoding="utf-8")
    marker = design.index(_DDL_MARKER)
    fence = design.index("```sql\n", marker) + len("```sql\n")
    end = design.index("\n```", fence)
    return design[fence:end]


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=10, isolation_level=None)
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("SQLite foreign-key enforcement is unavailable.")
    return connection


@contextmanager
def _connection(database_path: Path):
    connection = _connect(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _create_schema(database_path: Path) -> None:
    if sqlite3.sqlite_version_info < (3, 8, 0):
        raise RuntimeError("SQLite 3.8.0 or later is required for partial indexes.")
    with _connection(database_path) as connection:
        connection.execute(
            "CREATE TABLE accounts (account_id TEXT NOT NULL PRIMARY KEY)"
        )
        connection.executescript(_b2_schema_sql())


def _add_account(connection: sqlite3.Connection, account_id: str) -> None:
    connection.execute("INSERT INTO accounts(account_id) VALUES (?)", (account_id,))


def _add_provider_identity(
    connection: sqlite3.Connection,
    provider_identity_id: str,
    adapter_id: str,
    issuer: str,
    subject: str,
) -> None:
    connection.execute(
        """
        INSERT INTO provider_identity(
            provider_identity_id, adapter_id, issuer_namespace, subject_key
        ) VALUES (?, ?, ?, ?)
        """,
        (provider_identity_id, adapter_id, issuer, subject),
    )


def _add_pending_binding(
    connection: sqlite3.Connection,
    binding_id: str,
    account_id: str,
    provider_identity_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO account_identity_binding(
            binding_id, account_id, provider_identity_id, state, binding_version
        ) VALUES (?, ?, ?, 'PENDING', 1)
        """,
        (binding_id, account_id, provider_identity_id),
    )


@pytest.fixture
def schema_database(tmp_path: Path) -> Path:
    database_path = tmp_path / "synthetic-b2-schema.sqlite3"
    _create_schema(database_path)
    return database_path


def test_provider_identity_sql_constraints_match_application_validation(
    schema_database: Path,
) -> None:
    invalid_pairs = (
        ("", "subject"),
        ("issuer", ""),
        ("issuer", "   "),
        (" issuer", "subject"),
        ("issuer", "subject "),
        ("\u2003issuer", "subject"),
        ("issuer", "subject\u3000"),
        ("i" * 2049, "subject"),
        ("issuer", "s" * 1025),
    )

    with _connection(schema_database) as connection:
        for index, (issuer, subject) in enumerate(invalid_pairs):
            with pytest.raises(ValueError):
                ProviderIdentity(issuer, subject)
            with pytest.raises(sqlite3.IntegrityError):
                _add_provider_identity(
                    connection,
                    f"invalid-{index}",
                    "adapter-a",
                    issuer,
                    subject,
                )


def test_provider_identity_rejects_non_string_parts() -> None:
    for issuer, subject in (
        (None, "subject"),
        (b"issuer", "subject"),
        ("issuer", 1),
    ):
        with pytest.raises(ValueError):
            ProviderIdentity(cast(str, issuer), cast(str, subject))


def test_internal_whitespace_is_preserved_without_normalization(
    schema_database: Path,
) -> None:
    identity = ProviderIdentity("issuer\tinternal", "subject\tinternal")
    with _connection(schema_database) as connection:
        _add_provider_identity(
            connection,
            "provider-1",
            "adapter-a",
            identity.issuer,
            identity.subject,
        )
        stored = connection.execute(
            """
            SELECT issuer_namespace, subject_key
            FROM provider_identity WHERE provider_identity_id = ?
            """,
            ("provider-1",),
        ).fetchone()

    assert stored == (identity.issuer, identity.subject)


def test_provider_identity_tuple_is_unique_across_adapter_ids(
    schema_database: Path,
) -> None:
    with _connection(schema_database) as connection:
        _add_provider_identity(
            connection, "provider-a", "adapter-a", "issuer", "subject"
        )
        with pytest.raises(sqlite3.IntegrityError):
            _add_provider_identity(
                connection, "provider-b", "adapter-b", "issuer", "subject"
            )


def test_foreign_keys_are_enabled_on_each_connection(schema_database: Path) -> None:
    for _ in range(2):
        with _connection(schema_database) as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    with _connection(schema_database) as connection:
        _add_provider_identity(
            connection, "provider-a", "adapter-a", "issuer", "subject"
        )
        with pytest.raises(sqlite3.IntegrityError):
            _add_pending_binding(
                connection, "orphan-binding", "missing-account", "provider-a"
            )
        _add_account(connection, "account-a")
        with pytest.raises(sqlite3.IntegrityError):
            _add_pending_binding(
                connection, "orphan-provider-binding", "account-a", "missing-provider"
            )
        _add_pending_binding(connection, "binding-a", "account-a", "provider-a")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM accounts WHERE account_id = 'account-a'")


def test_invalid_binding_writes_preserve_state_and_version(
    schema_database: Path,
) -> None:
    with _connection(schema_database) as connection:
        _add_account(connection, "account-a")
        _add_account(connection, "account-b")
        _add_provider_identity(
            connection, "provider-a", "adapter-a", "issuer", "subject"
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO account_identity_binding(
                    binding_id, account_id, provider_identity_id, state, binding_version
                ) VALUES ('direct-active', 'account-a', 'provider-a', 'ACTIVE', 1)
                """
            )
        _add_pending_binding(connection, "binding-a", "account-a", "provider-a")
        with pytest.raises(sqlite3.IntegrityError):
            _add_pending_binding(
                connection, "duplicate-binding", "account-a", "provider-a"
            )

        invalid_updates = (
            ("ACTIVE", 1),
            ("ACTIVE", 3),
            ("FAILED", 3),
            ("REVOKED", 2),
        )
        for state, version in invalid_updates:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE account_identity_binding
                    SET state = ?, binding_version = ?
                    WHERE binding_id = 'binding-a'
                    """,
                    (state, version),
                )

        unchanged = connection.execute(
            """
            SELECT state, binding_version FROM account_identity_binding
            WHERE binding_id = 'binding-a'
            """
        ).fetchone()
        assert unchanged == ("PENDING", 1)

        connection.execute(
            """
            UPDATE account_identity_binding
            SET state = 'ACTIVE', binding_version = 2
            WHERE binding_id = 'binding-a'
            """
        )
        for mutation in (
            "binding_id = 'replacement-id'",
            "account_id = 'account-b'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"""
                    UPDATE account_identity_binding
                    SET {mutation}, state = 'PENDING_UNLINK', binding_version = 3
                    WHERE binding_id = 'binding-a'
                    """
                )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM account_identity_binding WHERE binding_id = 'binding-a'"
            )
        for column, value in (
            ("provider_identity_id", "replacement-provider"),
            ("adapter_id", "adapter-b"),
            ("issuer_namespace", "replacement-issuer"),
            ("subject_key", "replacement-subject"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"UPDATE provider_identity SET {column} = ? "
                    "WHERE provider_identity_id = 'provider-a'",
                    (value,),
                )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM provider_identity WHERE provider_identity_id = 'provider-a'"
            )


def test_active_pending_unlink_and_revoked_owner_block_competing_activation(
    schema_database: Path,
) -> None:
    with _connection(schema_database) as connection:
        _add_account(connection, "account-a")
        _add_account(connection, "account-b")
        _add_provider_identity(
            connection, "provider-a", "adapter-a", "issuer", "subject"
        )
        _add_pending_binding(connection, "binding-a", "account-a", "provider-a")
        _add_pending_binding(connection, "binding-b", "account-b", "provider-a")

        connection.execute(
            """
            UPDATE account_identity_binding SET state = 'ACTIVE', binding_version = 2
            WHERE binding_id = 'binding-a'
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE account_identity_binding SET state = 'ACTIVE', binding_version = 2
                WHERE binding_id = 'binding-b'
                """
            )

        connection.execute(
            """
            UPDATE account_identity_binding
            SET state = 'PENDING_UNLINK', binding_version = 3
            WHERE binding_id = 'binding-a'
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE account_identity_binding SET state = 'ACTIVE', binding_version = 2
                WHERE binding_id = 'binding-b'
                """
            )

        connection.execute(
            """
            UPDATE account_identity_binding SET state = 'REVOKED', binding_version = 4
            WHERE binding_id = 'binding-a'
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE account_identity_binding SET state = 'ACTIVE', binding_version = 2
                WHERE binding_id = 'binding-b'
                """
            )
        assert connection.execute(
            """
            SELECT state FROM account_identity_binding WHERE binding_id = 'binding-b'
            """
        ).fetchone() == ("PENDING",)


def test_same_owner_reactivation_reuses_retained_binding_history(
    schema_database: Path,
) -> None:
    with _connection(schema_database) as connection:
        _add_account(connection, "account-a")
        _add_provider_identity(
            connection, "provider-a", "adapter-a", "issuer", "subject"
        )
        _add_pending_binding(connection, "binding-a", "account-a", "provider-a")
        for state, version in (
            ("ACTIVE", 2),
            ("PENDING_UNLINK", 3),
            ("REVOKED", 4),
            ("PENDING", 5),
            ("ACTIVE", 6),
        ):
            connection.execute(
                """
                UPDATE account_identity_binding
                SET state = ?, binding_version = ?
                WHERE binding_id = 'binding-a'
                """,
                (state, version),
            )

        assert connection.execute(
            """
            SELECT state, binding_version
            FROM account_identity_binding WHERE binding_id = 'binding-a'
            """
        ).fetchone() == ("ACTIVE", 6)


def test_each_allowed_transition_accepts_only_the_next_version(
    schema_database: Path,
) -> None:
    with _connection(schema_database) as connection:
        _add_account(connection, "account-a")
        _add_provider_identity(
            connection, "provider-a", "adapter-a", "issuer", "subject"
        )
        _add_pending_binding(connection, "binding-a", "account-a", "provider-a")

        transitions = (
            ("FAILED", 2),
            ("PENDING", 3),
            ("ACTIVE", 4),
            ("PENDING_UNLINK", 5),
            ("ACTIVE", 6),
            ("PENDING_UNLINK", 7),
            ("REVOKED", 8),
            ("PENDING", 9),
            ("ACTIVE", 10),
        )
        for state, version in transitions:
            connection.execute(
                """
                UPDATE account_identity_binding
                SET state = ?, binding_version = ?
                WHERE binding_id = 'binding-a'
                """,
                (state, version),
            )

        assert connection.execute(
            """
            SELECT state, binding_version
            FROM account_identity_binding WHERE binding_id = 'binding-a'
            """
        ).fetchone() == ("ACTIVE", 10)


def test_concurrent_connections_allow_only_one_owner_to_activate(
    schema_database: Path,
) -> None:
    with _connection(schema_database) as connection:
        _add_account(connection, "account-a")
        _add_account(connection, "account-b")
        _add_provider_identity(
            connection, "provider-a", "adapter-a", "issuer", "subject"
        )
        _add_pending_binding(connection, "binding-a", "account-a", "provider-a")
        _add_pending_binding(connection, "binding-b", "account-b", "provider-a")

    start = Barrier(2)

    def activate(binding_id: str) -> bool:
        with _connection(schema_database) as connection:
            start.wait(timeout=5)
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE account_identity_binding
                    SET state = 'ACTIVE', binding_version = 2
                    WHERE binding_id = ? AND state = 'PENDING' AND binding_version = 1
                    """,
                    (binding_id,),
                )
                if cursor.rowcount != 1:
                    connection.execute("ROLLBACK")
                    return False
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                return False
            connection.execute("COMMIT")
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(activate, ("binding-a", "binding-b")))

    assert sorted(outcomes) == [False, True]
    with _connection(schema_database) as connection:
        rows = connection.execute(
            """
            SELECT state, COUNT(*)
            FROM account_identity_binding
            GROUP BY state
            """
        ).fetchall()
    assert dict(rows) == {"ACTIVE": 1, "PENDING": 1}
