# Security trust boundary: provider and search execution callbacks

## What the callback does and does not prove

`ChatService.respond` and `SearchService.search` accept an `execution_check` callable. A direct Python caller can supply a callable such as `lambda: True`; the services treat a true result as passing their local dispatch guard. That callable is not an independent authorization boundary and its return value is not proof of authorization by itself.

The HTTP path has a different trust source. HTTP request bodies are parsed as JSON data; a browser cannot transmit a live Python callable. The request handler resolves the opaque session cookie through the server-side session manager, constructs request context from that resolved session, then supplies a closure that re-resolves and revalidates the current session, identity/profile ownership, and applicable conversation/approval binding before execution. Anonymous chat is pinned to Local Demo. Search has its own check before adapter dispatch. Authenticated chat and approved continuation remain behind the existing unconditional runtime NO-GO gate.

Relevant implementation: [`futuristic_app.py`](interface/futuristic_app.py#L859), [`futuristic_app.py`](interface/futuristic_app.py#L463), [`futuristic_app.py`](interface/futuristic_app.py#L2301), [`futuristic_app.py`](interface/futuristic_app.py#L2413), [`chat/service.py`](chat/service.py#L280), [`chat/orchestration.py`](chat/orchestration.py#L693), [`research/service.py`](research/service.py#L38), and [`runtime_no_go.py`](safety/runtime_no_go.py#L29).

## In-process callers and plugins

The focused tests use fake provider/search adapters and demonstrate that a direct in-process caller supplying an always-true callback can reach those fakes. This does not demonstrate a remote HTTP bypass: an HTTP client cannot serialize or select that callback.

The current plugin contract is Python-object based. [`chat/plugins.py`](chat/plugins.py#L209) registers metadata and optional adapter objects in the same process; the repository contains no separate plugin process, untrusted plugin sandbox, or HTTP endpoint that loads and executes user-provided Python. The registry therefore does not isolate a plugin from the host. Any adapter code loaded into this process must be treated as trusted code; if it can obtain service references, it can call services and supply its own callback. This is a trust assumption, not a security guarantee.

## Required boundary if untrusted in-process execution is introduced

Do not treat an opaque boolean or a caller-provided function as a capability. A future untrusted extension would need an independently enforced process boundary: keep provider/search credentials and dispatch inside a host-owned process, authenticate and authorize each request at a narrow IPC boundary using server-owned, request-bound state, and restrict the extension process with appropriate OS isolation. A Python object token alone does not isolate arbitrary code running in the same interpreter. No authorization redesign is implemented by this document.
