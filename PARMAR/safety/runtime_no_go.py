"""Runtime denial policy for protected effects without an atomic authorization fence."""

from __future__ import annotations

from enum import Enum


class UnfencedOperation(str, Enum):
    """Protected operations that lack a shared authorization/effect boundary."""

    AUTHENTICATED_CHAT = "authenticated_chat"
    APPROVED_CHAT_CONTINUATION = "approved_chat_continuation"
    PROTECTED_MEMORY_MUTATION = "protected_memory_mutation"
    PROFILE_WORKSPACE_WRITE = "profile_workspace_write"
    SESSION_PROFILE_SELECTION = "session_profile_selection"


class RuntimeNoGo(RuntimeError):
    """Raised when a protected effect has no supported atomic authorization fence."""

    def __init__(self, operation: UnfencedOperation) -> None:
        self.operation = operation
        super().__init__(
            f"Protected operation '{operation.value}' is disabled: "
            "an atomic authorization fence is unavailable."
        )


def require_atomic_authorization_fence(operation: UnfencedOperation) -> None:
    """Deny unconditionally until the operation has a real shared atomic fence."""
    if type(operation) is not UnfencedOperation:
        raise TypeError("A known protected operation is required.")
    raise RuntimeNoGo(operation)
