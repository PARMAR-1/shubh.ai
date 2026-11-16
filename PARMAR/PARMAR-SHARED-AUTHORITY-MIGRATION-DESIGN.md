# PARMAR Shared Authorization Authority Migration Design

| Field | Decision |
|---|---|
| Status | Design proposal only; migration not approved, implemented, or demonstrated safe |
| Scope | A future local-only authorization transaction prototype; no production authentication or external execution |
| Current runtime | Phase 3G.1 NO-GO gates remain mandatory and unchanged |
| Data handling | No database, schema, runtime code, or existing migration was changed for this design |

## 1. Architecture decision

**Conditional recommendation:** if PARMAR later authorizes one local protected
write, use one SQLite database file and one `BEGIN IMMEDIATE` transaction as
the authority for every mutable fact needed to authorize that write and for the
write itself. For the first candidate, evaluate an explicitly consented local
memory insertion tied to one exact server-side approval. Do not select or enable
that operation until product, privacy, and security owners approve its meaning
and data handling.

The single transaction must read and validate the session, evidence, account,
approval, binding, expiry, and current authorization generation; claim the
approval; write the local effect and audit outbox record; mark the approval
consumed; then commit. Session/evidence revocation and approval cancellation
must contend on the same database writer lock and update the same records.
Their successful commit is the ordering point:

- If revocation or cancellation commits first, execution observes terminal or
  stale authority and performs no local write.
- If execution commits first, the local effect is already committed before
  revocation/cancellation. It is not retroactively undone.
- The approval claim and local effect are never committed as independent steps.

This is a proposed architecture, **not an implementation or demonstrated
guarantee**. The current stores cannot provide this boundary: each repository
owns a separate connection, database file, and transaction. SQLite
multi-database `ATTACH` is not proposed as a shortcut. PARMAR does not enforce
a common attached-connection writer path or a journal-mode/durability contract
for all participating stores; existing repository writes could bypass it.
Multi-file crash atomicity is not established by this codebase.

The current application also has no shared-authority coordinator or migration
mode. Session/evidence lifecycle is in the session DB; approval claims and
cancellation are in the approval DB; account, profile and binding facts are
in the identity DB; consent and memory rows are in the memory DB. A future
operation is not eligible merely because it calls a new repository: every
fact that can authorize or invalidate that operation, and its local effect,
must be read or changed through the same authority transaction. Direct calls
to legacy repositories and alternate routes must be removed, disabled, or
made incapable of performing that effect before any NO-GO gate can be
reconsidered.

### Alternatives considered

| Alternative | Assessment |
|---|---|
| Keep separate stores and add another pre-write authorization check | Repeats the check-to-effect race; reject. |
| Add a fencing generation to only the session or approval DB | The local effect remains in another DB. A check or generation copy cannot serialize with the effect; reject. |
| Attach the existing SQLite files to one connection | Would require every writer, including revocation and cancellation, to use the exact same coordinator and compatible durability configuration. Current APIs use independent connections; crash guarantees are unproven. Do not adopt without a separate SQLite/platform proof. |
| Make a new database authoritative only for a new local operation | Insufficient while session/evidence, account status/bindings, approval, or revocation remains authoritative elsewhere. Would be a shadow authority; reject. |
| Migrate all authorization facts and the selected local data to one authority | Only plausible option for the stated guarantee. Requires quiesced, integrity-checked migration, explicit identity mapping, cutover controls, and recovery evidence described below. Not approved or performed. |

## 2. Current stores and observed controls

Source inspected: [sessions.py](./sessions.py),
[identity_store.py](./identity_store.py),
[approvals.py](./interface/approvals.py),
[conversations.py](./conversations.py), [memories.py](./memories.py),
and [futuristic_app.py](./interface/futuristic_app.py).

| Store | Current schema | Existing transaction and integrity behavior relevant to migration |
|---|---|---|
| Sessions/evidence | `SQLiteSessionRepository` schema v4; `sessions` and `identity_evidence` are in one DB | Write methods use `BEGIN IMMEDIATE`. Session and evidence records have separate HMAC formats using the session integrity key. Existing v1→v2→v3→v4 migrations are additive except the v3→v4 policy that revokes active trusted sessions lacking evidence lifecycle records; old/unmapped sessions are not assigned an account. |
| Identity directory/account registry | `SQLiteIdentityRepository` schema v3; profiles, workspaces/memberships/shares, accounts, provider bindings | Own DB. Account IDs are constrained UUIDv4; triggers prevent account ID changes/deletion; provider bindings have a foreign key. Registry schema migration validates/copies v2 account/binding tables. These records do not have the session/approval HMAC scheme. No binding writer or active provider flow is configured. |
| Approvals | `SQLitePendingApprovalStore` schema v3; `pending_approvals` contains token hash, state, expiry, canonical payload and integrity tag | Own DB; execution claim and cancellation each use `BEGIN IMMEDIATE`. HMAC covers token hash, state, expiry and payload using an approval-specific derived key. Payload validates user/session, decision/request fingerprint, action binding, execution context, and cancellation audit. `EXECUTING` persists separately from the effect. |
| Conversations | `SQLiteConversationRepository` schema v3; conversations, messages and research metadata | Own DB; write methods use `BEGIN IMMEDIATE`; foreign keys and research metadata triggers are validated. v1/v2 migrations add research metadata and profile scoping. Legacy `profile_id = NULL` is preserved, not assigned. No per-record HMAC is present. |
| Memory and consent | `SQLiteMemoryRepository` schema v1; `memory_consents` and `memories` | Own DB; writes use `BEGIN IMMEDIATE`, and repository methods enforce consent/owner checks locally. Validation is structural/content-level; no per-record HMAC is present. No prior schema migration is supported beyond fresh v1 creation. |

The application selects these stores independently via
`PARMAR_SESSION_DB_PATH`, `PARMAR_IDENTITY_DB_PATH`,
`PARMAR_APPROVAL_DB_PATH`, `PARMAR_CONVERSATION_DB_PATH`, and
`PARMAR_MEMORY_DB_PATH` (each also has a distinct default file). Same-process
Python locks do not coordinate separate processes or SQLite files. Existing
HMACs protect specific encoded session/evidence/approval data; they are not a
general database authenticity guarantee for the other stores.

## 3. Proposed minimum authoritative schema

Use a new, explicitly configured authority DB with its own schema version and
format/key version. No existing DB should silently become authoritative merely
because a path happens to match. The minimum logical tables are:

1. **`accounts` / `provider_account_bindings`** — preserve immutable PARMAR
   account IDs and exact issuer/subject bindings, with the current uniqueness,
   foreign-key, immutable-ID, and no-delete constraints. Binding writes remain
   unavailable unless separately approved. Matching UUIDs or attributes is
   never evidence for account mapping.
2. **`sessions`** — retain session ID, token hash (never raw token), canonical
   account/user ID, source, timestamps, status, expiry, replacement relation,
   and any required active-profile context. Keep legacy unmapped sessions
   explicitly unmapped.
3. **`identity_evidence`** — evidence ID, account ID, bound session ID,
   authenticated/expiry timestamps, lifecycle state, and integrity metadata.
   Enforce one evidence record per session binding and terminal
   `EXPIRED`/`REVOKED`/`SUPERSEDED` states that cannot return to `ACTIVE`.
4. **`authorization_generations`** — monotonic generation for the exact
   authorization subject/scope. Session/evidence revocation, supersession, and
   relevant account/session invalidation update the generation in the same
   transaction as their state transition. A generation is not accepted from
   client input.
5. **`approvals`** — opaque token hash, owner/account, session and evidence
   IDs, action/resource/permission binding, request and scope fingerprints,
   expiry, lifecycle state, generation observed at issuance/approval, payload
   version, and integrity metadata. Never store or migrate the bearer token.
   The versioned state constraint must include a terminal
   `RECOVERY_REQUIRED` disposition that cannot authorize, be claimed, or
   transition back to a live state.
6. **Selected local effect and consent tables** — for the candidate memory
   operation, move the relevant consent row and memory record into this DB.
   The effect row records its owner, source approval ID (unique), provenance,
   timestamps, and content. Foreign keys and unique constraints prevent
   cross-account ownership and duplicate effect insertion.
7. **`authorization_audit_outbox`** — fixed, non-content audit metadata written
   in the same transaction as the state/effect change. A separate idempotent
   delivery worker may export it later; failure to deliver must not roll back
   the committed effect or authorize a retry. Do not put prompts, memory
   content, credentials, or raw tokens in the outbox.
8. **`migration_manifest`** — source DB identifiers/schema versions, migration
   run ID, counts/digests, key-format versions, verification state, and the
   explicit single-authority activation marker. It is operational metadata, not
   an authorization bypass.

Exact columns, constraints, HMAC envelopes, generation scope, audit retention,
and key-management rules require design review before schema approval. Tables
must not be populated from unverified client claims. The design must enforce
ownership using canonical account IDs and exact approval scope.

### Implementation acceptance gates

These are prerequisites, not claims about current code:

- Enumerate every read/write API and caller for each authorization fact and
  selected effect. The shared transaction must be the sole execution entry
  point; no legacy repository, route, background job, or direct service call
  can commit the selected effect outside it.
- Put every mutable predicate that can invalidate the selected operation
  (including its session/evidence state, account state if mutable, approval,
  relevant consent, and revocation/cancellation generation) in the authority
  DB. A predicate left authoritative in another store prevents the claim.
- Keep Phase 3G.1 guards unconditional for all other operations. A local
  prototype is not authority to enable chat, research, profile/workspace
  writes, sharing, or external effects.
- Run the ordering, restart, concurrency, storage-failure, and bypass tests in
  Section 8 against the actual repository APIs and all supported process
  configurations. A test-only transaction or mocked repository is not proof
  of the production call path.
- Obtain independent review of the schema, migration manifest, key handling,
  recovery behavior, and measured fault-injection results before any gate
  change.

### Synthetic-only prototype isolation and authorization

Any future synthetic-only prototype is a separately scoped, offline experiment.
It is not authorized by this design or by passing tests. Before execution, all
of the following are mandatory:

- Use generated synthetic identities, records, and ephemeral test keys only.
  Do not copy, import, transform, or derive fixtures from production data.
- Run in a separate disposable environment with isolated storage and explicit
  non-production database paths. Verify the environment and paths before
  launch; destroy the disposable environment and its generated data afterward.
- Start with a clean, allowlisted environment. Production credentials, secrets,
  configuration files, mounted secret stores, and credential helpers must be
  absent and inaccessible.
- Deny access to production databases by filesystem, mount, and network
  boundaries. Do not rely only on a different filename or an operator promise.
- Disable network egress and verify that no production service, endpoint, or
  database is reachable. No provider, production identity, account-binding,
  SSO, or production authentication flow may be configured or activated.
- Leave every Phase 3G.1 NO-GO guard unchanged. The experiment may not enable
  chat, research, profile/workspace writes, sharing, external effects, or any
  protected production operation.
- Obtain explicit, recorded human authorization before each execution. The
  authorization must name the responsible approver, purpose, scope, environment,
  duration, and cleanup plan. Automated test discovery or CI success is not
  authorization.
- Independently verify the isolation controls and retain evidence of those
  checks before seeking execution authorization. Any failed, missing, or
  ambiguous check means do not run the prototype.

These are requirements, not controls currently implemented or verified by this
repository. Until the controls are independently demonstrated and explicit
human authorization is recorded, synthetic-prototype execution remains
**NOT AUTHORIZED**. Meeting this gate does not prove historical key continuity,
owner mapping, production durability, recovery/cutover readiness, or authorize
real-data migration or production execution.

### Local execution transaction

The candidate local operation should be a single bounded function with no
network or provider calls:

1. Start `BEGIN IMMEDIATE` on the authority DB; fail closed on busy,
   corruption, integrity failure, or unavailable storage.
2. Read session/evidence and verify row integrity, active state, exact session
   and account binding, and all session/evidence/approval expiries using one
   trusted current time.
3. Read the approval by token hash and verify its integrity, owner, session,
   evidence, exact action/resource/scope, and expected generation.
4. Conditionally claim only a valid pending/approved state using an
   update-with-state-and-generation predicate. Any row-count mismatch denies.
5. Recheck the relevant explicit consent in this same transaction.
6. Write exactly one local effect, keyed uniquely to the approval, and insert
   the fixed-metadata audit outbox row.
7. Transition the approval to `CONSUMED` and commit. Any failure rolls back
   the claim, effect, outbox, and terminal state together.

For a wholly local SQL operation, `EXECUTING` may exist only inside the
uncommitted transaction; it must not be a committed, retryable gap before the
effect. If operational requirements demand a separately committed execution
claim, that is a different state machine and does not provide claim-plus-effect
atomicity. Do not make network calls, filesystem effects, or callbacks from
inside this transaction.

Revocation and cancellation take the same writer lock and update their
authoritative row and generation. They cannot retroactively undo a local write
that committed first. A local `COMMIT` error with uncertain outcome must be
resolved by querying the unique approval/effect ID after reopening the DB;
never blindly retry the effect. This behavior and the selected SQLite
journal/synchronous/filesystem durability assumptions require fault-injection
evidence before any guarantee is claimed.

## 4. Record-by-record migration mapping

Migration must preserve IDs and records where their authority is established.
It must not guess identity, profile assignment, status, or consent.

| Source record | Proposed treatment in a staged authority DB |
|---|---|
| Session v4 rows | Verify existing session HMAC with the configured session key; preserve session ID, token hash, source, timestamps, status and replacement relation. Recompute the destination envelope using a versioned destination key only after successful source verification. Do not copy raw cookies/tokens. |
| Older/unmapped sessions | Preserve as explicitly unmapped, with no account/evidence association. Preserve existing legacy migration policy: do not infer account from `user_id` equality; do not upgrade or authenticate legacy rows. If their source migration semantics cannot be reproduced, keep them archived and inaccessible. |
| Session/evidence pairs | Verify both HMACs and cross-check session ID, account identity, evidence ID, authentication interval, expiry and lifecycle state. Preserve each evidence UUIDv4 and terminal state. Reject duplicate evidence IDs or inconsistent bindings. Active evidence is eligible only if it remains valid at the frozen migration instant and is paired to exactly one active session; otherwise migrate terminal/deny state. |
| Identity accounts | Preserve exact immutable account UUIDv4, status and created time after schema/uniqueness checks. Keep account identity immutable and undeletable. Do not create accounts from arbitrary `user_id` values. |
| Provider account bindings | Preserve exact issuer/subject/account tuple and created time only after checking normalization, uniqueness, referenced account existence and duplicate conflicts. Never synthesize a binding, infer one from email/name/UUID, or activate a provider as part of migration. |
| Profiles/workspaces/memberships/shares | Preserve IDs, names, timestamps, role, owner, grantor and membership edges. Map owner/grantor to a canonical account only with independently verified provenance. Ambiguous owners, dangling edges, and cross-account sharing are quarantined/inaccessible; do not reassign profiles to the active account or derive access from workspace membership. |
| Approval rows in terminal states | Verify token-hash/state/expiry/payload HMAC and payload-level request, binding, owner/session, and cancellation-audit checks. Preserve as non-executable audit history; re-sign only in a versioned envelope after validation. |
| `PENDING` / `APPROVED` approvals | Safest default is invalidate at cutover and retain as terminal migration-invalidated audit records. They were created under the old execution boundary and must not silently become executable under a new generation or different storage authority. Require a fresh review/approval after cutover. |
| `EXECUTING` approvals | Convert to a terminal, non-retryable `RECOVERY_REQUIRED`/indeterminate disposition in the staged schema. Do not replay or auto-consume. The old state does not prove whether a provider call, local effect, or response release occurred. Preserve the original verified payload and lifecycle evidence for incident/reconciliation review. No automatic transition back to `PENDING` or `APPROVED`. |
| Conversation rows/messages/research | Preserve conversation/message/research IDs, roles, content, timestamps, profile scope and validated source payloads. Keep NULL/unprofiled legacy conversations NULL and never attach them to a guessed account/profile. Quarantine owner mappings that cannot be proven. Preserve current research foreign-key and assistant-only trigger invariants. |
| Memory consent and records | Preserve exact consent state/time and memory IDs/content/provenance/timestamps, with owner mapping only where canonical ownership is demonstrated. Do not turn missing consent into granted consent. Quarantine ambiguous owner rows rather than merging or reassigning them. |
| Other audit records | Preserve in place or migrate only under a separately documented retention/verification plan. Do not imply the JSONL or other external audit store participates in the SQLite transaction. Add new effect audit metadata to the proposed outbox. |

If source verification fails, the whole migration must not activate. Record
counts alone are insufficient: compare stable keyed record digests and
cross-table relationships. Any manual account/owner mapping requires a separate
approved, attributable mapping manifest and review; no automatic linking or
merging is allowed.

For each owner-bearing source table, create a mapping manifest keyed by
`(source database ID, table, primary key)`. Record the original owner value and
namespace, proposed canonical account ID (or an explicit `UNMAPPED` /
`QUARANTINED` disposition), independent provenance supporting the mapping,
reviewer, and decision time. A matching UUID, email, display name, or profile
label is not provenance. Do not authorize quarantined rows, but retain them
byte-for-byte (or as an authenticated, lossless export) in a separately
access-controlled archive with their source keys and digests.

For each table, reconcile the source primary-key set against the disjoint
destination and quarantine sets:

`source keys = migrated keys ∪ quarantined keys`

with no overlap, omission, or unexplained extra key. Also compare stable
keyed-record digests, row counts, foreign-key relationships, and the explicit
owner-mapping manifest. If any row cannot be preserved or reconciled, stop
before activation; do not silently drop it to make counts match. Any manual
account/owner mapping requires a separate approved, attributable mapping
manifest and independent review; no automatic linking or merging is allowed.

## 5. Integrity, keys, and backup/restore

### Before migration

- Inventory exact resolved database paths, schema versions, SQLite versions,
  journal/synchronous settings, file modes, keys/key IDs, active processes, and
  every writer including maintenance jobs.
- Use SQLite's online backup API for each source DB while the application is
  quiesced; do not copy a live DB file without accounting for journal/WAL state.
- Preserve source files as immutable, access-controlled backups. Encrypt backups
  at rest; keep encryption and integrity keys outside the DB and outside
  application logs.
- Record SHA-256 digests for closed backup artifacts and a signed/controlled
  manifest containing source path identifiers, schema versions, backup time,
  counts, and verification outputs. A file hash proves artifact identity, not
  semantic correctness.
- Verify `PRAGMA integrity_check`/`foreign_key_check`, application-level
  schema invariants, HMACs, approval payload bindings, evidence/session
  consistency, and owner/resource relationships against the backups. Do not
  start migration on the live source databases.

### Destination integrity

- Verify each source session/evidence/approval tag under its original key and
  exact legacy format before transforming a record.
- The inspected implementation does not persist a per-record key ID or expose
  a historical key ring for session/evidence or approval tags. The approval
  key is derived from the configured approval key, or the configured session
  key fallback, while session/evidence tags use the configured session
  integrity key. Therefore, a migration operator must inventory the exact
  historical key values, key purpose, derivation, and supported tag format
  from the controlled secret-management history, and map each source DB and
  schema era to the key required to verify it. This mapping is external
  migration evidence, not something inferable from a row.
- If the key used for any source row is unavailable, the source HMAC fails, or
  multiple key candidates produce ambiguity, stop migration and leave the
  source authoritative only under the existing NO-GO policy. Never skip
  verification, guess a key, or re-sign an unverifiable row. Test the complete
  key inventory against fixtures from every supported source schema/key era
  before migration.
- Preserve original signed/canonical payload bytes when possible. If fields or
  serialization must change, retain the verified source envelope/digest,
  create a new versioned destination envelope under a distinct key, and record
  the migration version and source reference. Never treat successful
  re-signing as proof the original record was authentic.
- Keep separate key purposes and explicit key IDs: session/evidence,
  approvals, destination authority, backup encryption, and audit signing.
  Rotation must support verification of the migration's source key while the
  migration is in progress. Do not reuse the new authority's key as a
  data-derived identity mapping.
- Re-run integrity, count, and relationship checks on the closed destination.
  Compare per-table keyed digests and critical invariants, not only total row
  counts. Require independent reviewer sign-off on discrepancies and the
  migration manifest.

### Restore

Restore the complete set of source backups as one frozen snapshot or restore
the new authority from its own consistent backup; never mix databases from
different points in time. Keep restored systems offline until all integrity
checks pass. After the new authority has accepted writes, restoring an older
snapshot could resurrect revoked sessions or approvals and omit committed
effects. Therefore post-activation recovery is **forward recovery from the
authority backup/journal**, not a silent rollback to stale independent stores.

## 6. Quiescence and cutover

No safe cutover is currently demonstrated. A future cutover requires an
approved maintenance window and an independently tested procedure:

1. **Preconditions:** approve the data model, owner mapping rules, local
   operation, schema, key custody/rotation, audit retention, SQLite durability
   settings, supported filesystem/platform matrix, RPO/RTO, and rollback
   authority. Exercise backup and restore on a sanitized copy.
2. **Quiesce all writers:** stop all application instances and background
   workers. Disable routes/jobs that can issue, rotate, expire, revoke, cancel,
   claim approvals, change consent, or mutate the selected data. Verify no
   process holds a write connection. A maintenance flag alone is insufficient
   unless every writer enforces it.
3. **Freeze and snapshot:** acquire exclusive migration control, prevent new
   database opens/writes, checkpoint/close each source DB as appropriate, and
   make consistent SQLite backups. Record the final source manifest. If any
   source changes during snapshot/verification, abort and restart the freeze.
4. **Build offline:** import only from backups into a new destination; preserve
   exact IDs and legacy null/unmapped states; verify HMACs and all mappings.
   Invalidate outstanding PENDING/APPROVED approvals and make EXECUTING
   approvals non-retryable/indeterminate. Do not process real requests.
5. **Reconcile:** compare row counts, keyed digests, HMAC results, uniqueness,
   foreign keys, expiry/revocation outcomes, account mappings, consent, and
   quarantined records. Independent reviewers must accept the manifest.
6. **Atomic authority switch:** while all writers remain stopped, replace the
   application configuration/deployment artifact so every authorization,
   revocation, cancellation, and selected local write uses only the new
   authority. Stop and verify termination of every old application and worker
   process; closing only the migration process is insufficient. Revoke the
   service account's write access to old DB files, make the archival copies
   read-only, and prevent old binaries/configurations from starting against
   them. The new process must verify the expected authority ID and schema
   version at startup and refuse mixed paths or a missing/partial migration
   manifest. Reopen traffic only after these checks pass. The switch is one
   controlled activation, not a period of dual-read or dual-write.
7. **Smoke test before reopening:** use synthetic accounts and a dedicated
   disposable authority DB; prove transaction ordering, failure behavior,
   restart behavior, integrity handling, and no access to production data.
   Verify all runtime NO-GO gates still deny unproven operations.
8. **Reopen only the approved local prototype:** after explicit change/security
   approval. Keep all other protected routes, provider dispatch, research, and
   writes disabled. Observe and audit the first operations; halt on any
   discrepancy.

There must be no dual-write period, fallback-to-old-store behavior, or
read-from-old/write-to-new split for authorization facts. If one participant
continues to mutate a legacy authority, the shared-boundary claim is invalid
and the prototype stays NO-GO.

Rollback is allowed only before the new authority accepts any request or
mutation: stop the candidate, verify no new authority writes occurred, then
restore the unchanged source set as one offline snapshot. After the first
accepted operation, do not roll back to those sources or to a pre-cutover
snapshot; that could erase effects and resurrect approvals/sessions. Freeze
service and forward-recover the new authority from its consistent backup and
verified journal, or remain unavailable. Any reverse migration would require
its own independently reviewed, lossless protocol and cannot be an emergency
fallback.

## 7. Crash recovery and `EXECUTING`

In the current design, `EXECUTING` is committed in the approval DB before the
provider or later write and can survive a crash independently of the effect.
On restart it cannot distinguish “not started,” “in flight,” “effect
committed,” or “output already released.” A timeout is not proof of no effect.
Migration must therefore make every such record non-executable and
`RECOVERY_REQUIRED`/indeterminate. It must not reset it to pending, retry it,
or claim cancellation/rollback.

The target schema must represent this explicitly before migration is run:
version the approval-state constraint to include `RECOVERY_REQUIRED`; allow
only a transition into that state from migrated `EXECUTING`; make it terminal
and ineligible for every inspect/authorize/claim/consume path; and expose it
only as a redacted audit/reconciliation status. Preserve the verified source
state, payload digest, source DB identifier, and migration reason. A recovery
operator may append a separately audited reconciliation note, but cannot
transition the approval back to executable or infer that no effect occurred.
If the target migration cannot encode and enforce these rules, abort cutover.

In the proposed local-only transaction, approval claim, local effect, audit
outbox row, and `CONSUMED` state commit together. A process crash before commit
should leave none of those transaction changes visible; a crash after a durable
commit should leave all visible. This remains a hypothesis until crash
injection verifies the chosen SQLite mode, filesystem, hardware, and startup
recovery behavior. On restart, resolve an uncertain client outcome by querying
the unique approval/effect identifier. Never replay solely because the client
did not receive a response.

External provider calls and research are outside that transaction. They remain
NO-GO; SQLite cannot atomically commit, cancel, or roll back a remote action.

## 8. Required adversarial acceptance tests

These are required before a future implementation is eligible for separate
security review. They are a plan only; none were added or run for this design.

1. **Revocation ordering:** use barriers on separate connections/processes.
   Revocation obtains/commits the authority transaction first; execution then
   denies and the selected write/outbox/claim leave no rows. Reverse ordering:
   local execution commits first; revocation follows; assert the effect is
   ordered before revocation and never represented as cancelled/rolled back.
2. **Cancellation ordering:** cancellation-first blocks claim and effect;
   effect-first leaves approval consumed and cancellation cannot claim it
   cancelled. Verify each response matches the committed state.
3. **Duplicate claimants:** synchronize concurrent processes on one approval;
   exactly one local write and one consumed approval are committed. Every other
   claimant denies without a second effect.
4. **Stale fencing generation:** issue an approval at generation N, revoke or
   supersede to N+1, then attempt execution with N. It must fail before
   touching effect/audit rows. Client-supplied generation changes are rejected.
5. **Replay and scope:** replay same approval, substitute account/session,
   evidence, action, resource, profile, permission, or content fingerprint.
   Assert no effect and no cross-account disclosure.
6. **Evidence lifecycle:** expired, revoked, superseded, unmapped, malformed,
   mismatched, or HMAC-invalid session/evidence must fail closed. Rotation must
   not revive the source evidence or stale generations.
7. **Crash boundaries:** terminate/kill process before claim, after conditional
   claim but before effect, after effect but before consume, during commit, and
   immediately after commit. For a single transaction, inspect after reopen:
   either all transaction rows committed or none; never auto-retry an unknown
   outcome.
8. **Storage faults:** busy/locked DB, disk full, read-only path, corrupt page,
   failed HMAC, invalid schema, and failed commit must produce fail-closed
   behavior with no success-shaped response or fallback to an old store.
9. **Migration interruption:** interrupt each offline migration stage; prove
   source backups remain unchanged, destination is not activated until full
   verification, and startup refuses partial/mixed authorities.
10. **Outbox idempotency:** duplicate delivery, exporter crash, and retry must
    not duplicate sensitive content or change authorization/effect outcome.
11. **Legacy/account isolation:** preserve evidence UUIDs and account bindings;
    do not create a binding from matching email/UUID; leave ambiguous legacy
    owner rows quarantined; preserve NULL profile scope and consent state.
12. **No bypass:** direct repository/service access and all alternate routes
    must continue to be blocked unless they enter the reviewed shared
    transaction. Existing Phase 3G.1 denial tests remain required.
13. **Migration completeness and key continuity:** remove, duplicate, or alter
    a source owner row; omit a historical key; supply the wrong key or tag
    format; and create a conflicting owner mapping. Migration must abort
    before activation, with exact source/destination/quarantine key-set
    reconciliation and no re-signing of unverifiable records.
14. **Recovery disposition:** migrate `EXECUTING` rows to
    `RECOVERY_REQUIRED`, restart every service, and attempt inspect,
    authorization, claim, consume, replay, and cancellation paths. All
    execution paths must deny; the state remains terminal and only a redacted
    audit status is exposed.
15. **Cutover exclusion:** leave an old process or worker alive, start with a
    stale authority path, or attempt to write an archived DB after activation.
    Cutover/startup must fail closed, and the old DB must not accept service
    writes.

## 9. Operations that remain NO-GO

Until architecture, migration, implementation, and independent review gates
pass, retain the unconditional Phase 3G.1 NO-GO for:

- Authenticated chat, conversation creation/append as part of chat, and
  provider dispatch.
- Approved chat/research continuation and any external search/provider call.
- Protected memory and consent mutations (including the proposed prototype
  until separately approved and reviewed).
- Profile/workspace creation, sharing, and session profile selection.
- Any operation whose authorization depends on identity, approval, consent, or
  ownership in a store outside the transaction that commits its effect.

Public stateless analysis, anonymous local simulation, and existing same-store
logout/revocation remain within their existing contracts. No production
identity provider, authentication, account linking/merging, deployment, or
AETHER change is authorized by this design.

## 10. Blockers and readiness

The design identifies a plausible single-file transaction boundary, but a safe
migration is **not demonstrated**. Unresolved prerequisites include exact
canonical mapping for every legacy owner and profile, the authoritative
account/session relationship, key custody and re-signing policy, audit
retention, handling of non-SQLite audit files, SQLite journal and durability
settings across supported environments, backup/restore RPO/RTO, and an
approved cutover/rollback authority.

Before any implementation claims crash/locking guarantees, define and record
the supported SQLite version, effective journal mode, `synchronous` setting,
busy/timeout behavior, foreign-key enforcement on every connection, filesystem
and storage class, and process model. Validate the effective settings at
startup and fail closed if they differ from the reviewed profile. Test
independent processes (not only threads) under writer contention, process
termination at commit boundaries, disk-full/read-only/corruption conditions,
backup/restore, and the actual supported filesystems. Do not claim durability
on network, synchronized, or otherwise unvalidated filesystems. `BEGIN
IMMEDIATE` serializes writers to one SQLite database; it does not by itself
prove power-loss durability, filesystem behavior, or atomicity with another
database or external effect.

Until those decisions and test evidence exist, do not implement a shadow
authority, change an existing database path, consolidate files, enable the
local operation, or remove any runtime NO-GO. This proposal is not evidence of
atomicity and is not production readiness.
