from datetime import datetime, timedelta, timezone

from PARMAR.identity_contracts import (
    AccountIdentity,
    AuthenticationContext,
    AuthenticatedPrincipal,
)


def trusted_session(manager, account_identity=None, *, now=None):
    account_identity = account_identity or AccountIdentity.generate()

    class Resolver:
        def resolve(self, _evidence, *, now=None):
            authenticated_at = now or datetime.now(timezone.utc)
            return AuthenticatedPrincipal(
                account_identity,
                AuthenticationContext(
                    authenticated_at,
                    authenticated_at + timedelta(minutes=5),
                ),
            )

    credentials = manager.create_identity_session(
        Resolver(), "test-evidence", now=now,
    )
    assert credentials is not None
    return credentials
