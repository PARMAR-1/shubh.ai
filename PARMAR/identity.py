"""Server-established identity contracts for PARMAR HTTP requests.

Principals are created by trusted server-side resolvers, never deserialized from
client input. Identity does not carry permissions or PARMAR safety decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from PARMAR.identity_contracts import (
    AccountIdentity,
    AuthenticationAdapter,
    AuthenticatedPrincipal,
    ProviderAccountBindingLookup,
    resolve_authenticated_principal,
)

LOCAL_DEMO_SOURCE = "local-demo"
TRUSTED_IDENTITY_SOURCE = "parmar-trusted-identity"


@dataclass(frozen=True)
class Principal:
    """Identity only; authorization and safety remain separate authorities."""

    authenticated: bool
    user_id: UUID | None
    authentication_source: str
    account_identity: AccountIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.authenticated, bool):
            raise ValueError("Principal authentication state must be boolean.")
        if not isinstance(self.authentication_source, str) or not self.authentication_source.strip():
            raise ValueError("Principal authentication source is required.")
        if self.authenticated:
            if not isinstance(self.user_id, UUID):
                raise ValueError("Authenticated principals require a server-established UUID.")
            if self.authentication_source.casefold() == LOCAL_DEMO_SOURCE:
                raise ValueError("Local-demo cannot identify an authenticated principal.")
            if (
                self.authentication_source == TRUSTED_IDENTITY_SOURCE
                and self.account_identity is None
            ) or (
                self.account_identity is not None
                and (
                    self.authentication_source != TRUSTED_IDENTITY_SOURCE
                    or type(self.account_identity) is not AccountIdentity
                    or self.account_identity.value != self.user_id
                )
            ):
                raise ValueError("Canonical account identity must match the server-established principal.")
        elif self.user_id is not None or self.authentication_source != LOCAL_DEMO_SOURCE:
            raise ValueError("Anonymous principals must use the local-demo source and no user ID.")
        elif self.account_identity is not None:
            raise ValueError("Anonymous principals cannot contain a canonical account identity.")

    @classmethod
    def anonymous_local_demo(cls) -> Principal:
        """Represent local demo use without inventing an account identity."""
        return cls(
            authenticated=False,
            user_id=None,
            authentication_source=LOCAL_DEMO_SOURCE,
        )

    @classmethod
    def authenticated_user(
        cls,
        user_id: UUID,
        authentication_source: str,
        *,
        account_identity: AccountIdentity | None = None,
    ) -> Principal:
        """Build a future authenticated principal from trusted server identity data."""
        return cls(
            authenticated=True,
            user_id=user_id,
            authentication_source=authentication_source,
            account_identity=account_identity,
        )


@dataclass(frozen=True)
class HTTPRequestContext:
    """Server-created request context; intentionally not part of ChatContext."""

    principal: Principal
    session_id: UUID | None = None
    active_profile_id: UUID | None = None
    authenticated_principal: AuthenticatedPrincipal | None = None

    def __post_init__(self) -> None:
        if self.principal.authenticated != isinstance(self.session_id, UUID):
            raise ValueError("Authenticated request contexts require an active server session.")
        if self.active_profile_id is not None and (
            not self.principal.authenticated or not isinstance(self.active_profile_id, UUID)
        ):
            raise ValueError("Active profiles require an authenticated server session.")
        canonical_identity = self.principal.account_identity
        if (canonical_identity is None) != (self.authenticated_principal is None):
            raise ValueError("Canonical identity and authenticated principal must be bound together.")
        if self.authenticated_principal is not None and (
            self.principal.user_id != self.authenticated_principal.account_identity.value
            or canonical_identity != self.authenticated_principal.account_identity
        ):
            raise ValueError("Request principal does not match the trusted account identity.")


class PrincipalResolver(Protocol):
    """Resolve a principal from trusted request credentials on the server."""

    def resolve(self, request: object) -> Principal:
        """Return a principal established by trusted server-side code."""


class ProviderBoundIdentityResolver:
    """Resolve provider evidence only through a verified adapter and existing binding."""

    def __init__(
        self,
        adapter: AuthenticationAdapter | None,
        bindings: ProviderAccountBindingLookup | None,
    ) -> None:
        self._adapter = adapter
        self._bindings = bindings

    def resolve(
        self,
        evidence: object,
        *,
        now: datetime | None = None,
    ) -> AuthenticatedPrincipal | None:
        if (
            self._adapter is None
            or self._bindings is None
            or not callable(getattr(self._adapter, "verify", None))
            or not callable(getattr(self._bindings, "find_existing_account", None))
        ):
            return None
        return resolve_authenticated_principal(
            self._adapter,
            self._bindings,
            evidence,
            now=now,
        )


class LocalDemoPrincipalResolver:
    """Non-production resolver that always returns anonymous local-demo identity."""

    def resolve(self, request: object) -> Principal:
        del request
        return Principal.anonymous_local_demo()