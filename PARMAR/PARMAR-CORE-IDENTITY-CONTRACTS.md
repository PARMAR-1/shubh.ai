# PARMAR Core Identity Contracts

Status: provider-neutral code contract only. This document does not approve or
activate production authentication, a provider, cross-product SSO, or account
migration. The architecture and approval gates in
[PARMAR-IDENTITY-IMPLEMENTATION-BASELINE.md](./PARMAR-IDENTITY-IMPLEMENTATION-BASELINE.md)
remain authoritative.

## Core contract

`identity_contracts.py` defines immutable `AccountIdentity`, distinct
`ProfileIdentity` and `WorkspaceIdentity` values, provider `(issuer, subject)`
identity, a time-bounded `AuthenticatedPrincipal`, and internal session and
product context types. Account IDs are generated as RFC 4122 UUIDv4 values and
serialize only as canonical lowercase UUID strings. Parsing rejects other UUID
versions and noncanonical strings. UUID equality across identity types does not
make those identities interchangeable.

`AuthenticationAdapter` verifies a provider assertion without choosing a
provider or returning a PARMAR account ID. `ProviderAccountBindingLookup` is
read-only and keyed only by the verified provider identity. An unknown binding
fails closed; there is no account creation, linking, merging, email/name
matching, or provider-to-resource permission path in these contracts.

The Core principal contains the PARMAR account identity and authentication
time/expiry only. Active profile and workspace membership are separate session
context. Missing, malformed, not-yet-valid, or expired principals deny access.
`authorize_resource_access` denies by default and validates owner/profile
context. View, Edit, and Manage have distinct action-coverage semantics, but a
`ResourceGrant` descriptor is not proof of an active or authoritative grant:
the evaluator denies non-owner access even when such a descriptor is present.
Grant lifecycle, revocation, and authority remain deferred. Workspace
membership alone never authorizes a resource; non-owner workspace-resource
sharing remains denied until its policy and approval gates are complete.

`ProductSessionContext` scopes the same Core session identity to a server-chosen
product audience; it contains no bearer token or product permission. Resource
authorization remains a separate product decision. The Home resolver contract
accepts a trusted server request and requests the fixed PARMAR Home audience;
the browser's account/profile/workspace fields are not an authentication source.

## Product boundaries

| Consumer | Contract relationship and current boundary |
| --- | --- |
| **PARMAR Home** | First-class future consumer of the shared account/session contract and the independent central PARMAR workspace. Home is not part of VOKKI or AETHER. Its current SPA reads `/api/session` and profile/workspace APIs; those existing server paths still use the legacy `Principal.user_id` development identity. Task 37 adds an internal account-registry foundation, but there is no approved authentication/account resolver or binding writer from that legacy ID to `AccountIdentity`. Do not equate or migrate those UUIDs. Before production Home identity integration, add an approved server-side account/session resolver and test its ownership/profile isolation. Do not send an account ID from the browser as proof. Home UI changes are out of scope. |
| **VOKKI** | Independent product workspace and future consumer of the same PARMAR account contract. Existing routes still use legacy server-side `user_id` ownership; the new registry is not wired to VOKKI routes and does not migrate those records. A vetted account resolver and the complete route/resource authorization gate remain prerequisites; workspace membership is not a personal-data grant. |
| **AETHER Desktop** | Independent product. Any future identity context belongs to a trusted application/control-plane boundary, not browser content. Browser identity and PARMAR account identity remain distinct. The browser preload exposes no PARMAR account, authentication, session, or token API; remote browsing remains disabled until independent isolation verification. |
| **AETHER Android** | Independent offline product. `AETHER_ANDROID_FUTURE_AUTH` names only a future authenticated architecture audience. It does not activate identity, network access, or online SSO. Preserve no `INTERNET` permission, no WebView, and no networking implementation. |
| **Future PARMAR products** | Use the same Core account identity and a server-selected product audience; they do not create product accounts. Their authorization and integration must be separately reviewed. |

This is a product-facing contract, not a live shared session or SSO flow.
Provider selection, production authentication topology, session handoff,
cross-product logout/revocation, sensitive-action step-up rules, and any
legacy-identity migration remain subject to the explicit approval gates in the
architecture baseline. The current authentication context does not define a
step-up policy for sensitive actions.

## Account registry foundation

The existing `SQLiteIdentityRepository` is extended additively; the registry
does not create another database. Schema version 3 adds an `accounts` table
with a random immutable UUIDv4 account ID, UTC creation timestamp, and
`ACTIVE`/`DISABLED` status, plus a `provider_account_bindings` table with a
unique `(issuer, subject)` key and a restrictive account foreign key. Version 1
profile/workspace data is preserved without assigning any legacy `user_id` to a
PARMAR account. Version 2 registry tables are rebuilt transactionally with
stricter database-level UUID and provider-identity constraints; invalid
existing registry rows abort migration without changing the prior schema.
Database triggers prevent account ID changes and account deletion; unsupported
or malformed records fail closed. Schema migration is additive and does not
provide a destructive downgrade path.
`ACTIVE` is only the registry-record lifecycle state; it does not assert that
the account authenticated or may access any product. No status-transition
operation is exposed.

`TrustedInternalAccountProvisioner` is an internal server-side interface that
mints account IDs; it accepts neither an account ID nor provider claims. It is
not wired to an HTTP route, development login, or an authentication adapter.
It currently has no application caller. This is an internal code boundary,
not an enforced capability or a production-hardened provisioning service:
in-process Python code can construct the provisioner and Python identity
objects are not unforgeable capabilities. Do not activate enrollment until an
approved account-provisioning policy and the applicable authentication,
security, and release gates are satisfied.
`AccountRegistry` exposes only read operations and implements the Task 36
provider-binding lookup contract. There is no binding writer, provider adapter,
active provider flow, or attribute-based linking. The binding table remains
empty until a separately approved authentication adapter and explicit
linking/provisioning policy are implemented. Account creation is a storage
foundation, not production authentication or account enrollment.

`identity.py` now defines a provider-bound resolver boundary, and
`SessionManager.create_identity_session` can persist only a current
`AuthenticatedPrincipal` returned by a supplied server-side resolver. The
canonical account ID, authentication time/expiry, and UUIDv4 evidence ID are
HMAC-bound to the session record; SQLite session schema v4 stores the
single-session evidence lifecycle alongside the session.
Existing v1/v2 sessions migrate as unmapped and are never assigned an account
by UUID equality. HTTP request context derives the canonical principal from
that persisted session, while client actor/account identity claims are rejected.
No resolver, provider adapter, or account-binding writer is configured in the
application, so these internal interfaces do not enable sign-in or production
authentication. They are not unforgeable in-process capabilities.

Task 36 identity dataclasses, including `VerifiedProviderIdentity` and
`AuthenticatedPrincipal`, are ordinary constructible in-process values. Their
types validate shape and time semantics, not unforgeable authentication
provenance. A future adapter and its callers must remain inside a trusted
server-side boundary and must never treat deserialized client objects as proof.

## Trusted identity evidence lifecycle

`AuthenticationContext` includes an RFC 4122 UUIDv4 `evidence_id`, generated
fresh by default. Session issuance and resolution reject malformed identity or
time fields, evidence authenticated in the future, and evidence at or past its
expiry. The SQLite session repository persists an HMAC-protected evidence
record with one account, one session binding, the authentication interval, and
one explicit lifecycle state:

| State | Meaning and allowed outcome |
| --- | --- |
| `ACTIVE` | Fresh evidence is bound to exactly one active protected session. Session rotation atomically moves this binding to the replacement session; it does not mint or revive evidence. |
| `EXPIRED` | Authentication evidence or its bound session reached expiry. It cannot issue or restore a session. |
| `REVOKED` | The session or its evidence was revoked. Revoking either side atomically invalidates the other. |
| `SUPERSEDED` | The trusted server explicitly invalidated this evidence in favor of newer evidence. It cannot authorize a session or protected action. |

Evidence IDs are single-use: replaying an ID, including under another PARMAR
account, cannot create a second session. Terminal states do not transition back
to `ACTIVE`. Session expiry, logout/revocation, evidence revocation, evidence
supersession, and rotation are serialized with the associated lifecycle record.
The resolver and request boundary recheck current evidence state and expiry;
invalid or inconsistent persisted evidence fails closed. Existing active trusted
sessions from schema v3 are revoked during migration because they have no
single-use lifecycle record. Existing non-identity sessions remain unmapped.
The resolver must preserve one evidence ID across retries/serialization of the
same authentication result and must not mint a new ID simply by reprocessing
the same assertion. Since `evidence` is opaque at this boundary, this code
cannot independently detect a provider assertion replayed and re-verified as
if it were new; provider-specific freshness, nonce, and replay checks remain
mandatory.

The in-memory repository provides equivalent process-local behavior for tests
and development only; its lifecycle does not survive process restart. The
SQLite implementation persists local lifecycle state and authenticates records
with the configured session-store integrity key. This is not a distributed
revocation service, production key-management design, provider revocation
source, or guarantee that already-running in-flight work is cancelled.

## Atomic execution fencing assessment (NO-GO)

The current boundary checks are not atomic with consequential writes or
external calls. `SQLiteSessionRepository` serializes session/evidence creation,
revocation, expiry, and rotation in its own session database transaction.
`SQLitePendingApprovalStore` independently serializes approval state in the
approvals database. Conversation/message writes, memory/consent writes, and
profile/workspace writes use independent repository connections and database
files (`PARMAR_CONVERSATION_DB_PATH`, `PARMAR_MEMORY_DB_PATH`,
`PARMAR_IDENTITY_DB_PATH`, `PARMAR_SESSION_DB_PATH`, and
`PARMAR_APPROVAL_DB_PATH`, respectively; each has its own default file).
Repository APIs open and commit their own transactions; they do not accept a
session transaction, connection, or authorization fence. Research/provider
calls are outside SQLite altogether.

| Execution path | Authorization/lifecycle boundary | Side-effect boundary | Decision |
| --- | --- | --- | --- |
| `/api/chat` with an authenticated account | Session/evidence in session DB; profile in identity DB | Conversation creation/append, memory read, research, provider call | **Runtime-enforced NO-GO** before conversation writes, memory retrieval, or provider execution |
| `/api/approval` approved chat continuation | Session/evidence, approval state, profile/conversation ownership | Research/provider call and conversation append | **Runtime-enforced NO-GO** before execution claim or provider/research continuation |
| `/api/memory` protected mutations | Session/evidence in session DB | Consent and memory mutation in memory DB | **Runtime-enforced NO-GO** before memory or consent mutation |
| Profile/workspace creation/sharing | Session/evidence in session DB | Identity directory mutation in identity DB | **Runtime-enforced NO-GO** before identity writes |
| Session profile selection | Session/evidence in session DB; profile ownership lookup in identity DB | Session profile update in session DB | **Runtime-enforced NO-GO** before profile selection |
| Session logout/revocation | Session/evidence and status in session DB | Session revocation in session DB | Supported; session/evidence mutation is serialized within its database |

Phase 3D re-resolves the same session, account, evidence, profile, approval
binding, and conversation ownership before protected stages. The approval
store now atomically claims an approval by transitioning it to `EXECUTING`;
only one concurrent request can claim it. Every subsequent approval check
requires that state, cancellation revokes it, and completion consumes it.
This store-local claim prevents duplicate concurrent approval claims and stale
or cancelled approval retries. It does **not** make the claim atomic with a
session revocation or a provider/write operation.

No monotonically increasing authorization generation or side-effect-store
fencing token is implemented. Adding a generation check before a write would
retain the same check-to-write race and would be misleading. A revocation can
commit after a check and before a later repository mutation or provider call;
cancellation can likewise race after an approval check. An in-progress
provider call cannot be interrupted or rolled back. Subsequent stage checks
deny when they observe revocation, but cannot recall an already committed
write, sent request, or accepted in-flight operation.

## Runtime policy and supported local/demo behavior

The shared runtime policy in `PARMAR/safety/runtime_no_go.py` unconditionally
denies the unfenced protected operations listed above. The policy has no
environment-variable, request-body, browser setting, development-auth, or
provider-configuration override. HTTP routes check it before protected
conversation creation, approval execution claim, memory/consent mutation,
profile/workspace writes, or profile selection. Approved continuation also
checks the policy in `ChatService.respond_approved` and the private approved
review entry to prevent direct service-call bypass. Denials return HTTP 503.

Supported behavior is limited to public stateless analysis, anonymous local
demo chat/simulation that does not persist protected state, and same-store
logout/session revocation. These allowances are not trusted identity, user
account authorization, production sign-in, or a shared atomic fence. Runtime
denial blocks the operation; it does not solve the cross-store race.

Authenticated chat provider/research execution, approved chat continuation,
protected memory mutations, profile/workspace writes, and profile selection
remain blocked pending a genuine shared atomic fencing architecture. The
application has no queued/background executor in this scope.

To clear NO-GO, introduce an authoritative execution service that performs
the current authorization decision and local protected mutation in one
transaction/linearizable store, with a durable monotonic fence checked by each
executor. External side effects additionally need a downstream fencing or
idempotency contract, cancellation semantics, and explicit in-flight status.
Then prove revocation-first and effect-first race orderings, retries, crashes,
and storage failures. No such common transaction, distributed lock, provider
revocation source, or downstream fencing contract exists here.

## Requirements before a trusted production provider or revocation source

This lifecycle code does not select, configure, or activate Google, Apple,
email, or any other provider. Before a production provider can be trusted,
product/security/privacy/legal/operations owners must approve the provider and
protocol, assurance and recovery rules, trusted server-side verification
boundary, signing/key rotation and storage, issuer/audience/nonce and replay
checks, clock/skew policy, account-binding creation policy, session/device
lifetime, and failure/reauthentication behavior. Account linking, automatic
merging, email/name matching, or converting development identities to
production accounts remain prohibited absent separate approval.

A production revocation source must be independently authenticated and
authoritative for the evidence/account lifecycle it claims to revoke. Its
contract must define stable evidence/session/device identifiers, event
ordering/versioning, event authenticity, idempotency, freshness and maximum
propagation delay, handling of duplicate/out-of-order/missing events, outages
and stale status (fail closed for protected use), cross-process and
cross-product fan-out, audit/privacy/retention, key rotation, restart/restore,
and races with session issuance, rotation, and protected actions. Acceptance
must prove that revocation prevents subsequent protected decisions across all
active sessions and products within an approved measurable target, without
claiming to cancel work already committed or in flight. None of these
provider or revocation-source integrations is implemented or configured here.

## Implemented and unimplemented boundary

Implemented code includes typed identity/context contracts; the persistent
account-record and provider-binding lookup foundation; the dormant trusted
identity resolver/session-binding boundary; time validity; Home context
projection; permission-level comparison; and a deny-by-default, owner-only
authorization evaluator. Focused tests exercise actual SQLite creation,
lookup, reopen, migration, uniqueness, rollback, and concurrency as well as
HTTP client-identity rejection and canonical session ownership.

No production provider adapter, account-to-provider binding writer, legacy ID
mapping, Home or VOKKI production account resolver, cross-product token/session
flow, authoritative grant resolver or workspace-sharing policy, Desktop
identity handoff, or Android online identity exists as a result of this
contract. The existing Home server APIs remain development identity APIs until
an approved server-side authentication/account resolver and resource-ownership
integration are implemented.
