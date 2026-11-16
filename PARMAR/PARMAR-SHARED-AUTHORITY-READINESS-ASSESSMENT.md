# PARMAR Shared Authority Readiness Assessment

**Purpose:** Consolidated assessment of Phases 3G.7–3G.10 from repository source, schema definitions, docs, backup tooling, and test-source inventory. This is a static evidence review. No live database, secret, runtime process, filesystem profile, or production configuration was inspected. No tests or migrations were run.

## Readiness summary

| Area | Status | What is established | Critical gap |
|---|---|---|---|
| 3G.7 Historical HMAC key continuity | **NOT PROVEN** | Current source key paths, derivations, formats, and verifiers are identified in [sessions.py](./sessions.py) and [interface/approvals.py](./interface/approvals.py). | No authoritative historical key custody/rotation evidence, per-row key IDs, or database/schema-era-to-key mapping. |
| 3G.8 Historical owner/account mapping | **NOT PROVEN** | Owner/account fields and current schema relationships are inventoried in [identity_store.py](./identity_store.py), [sessions.py](./sessions.py), [interface/approvals.py](./interface/approvals.py), [conversations.py](./conversations.py), and [memories.py](./memories.py). | No historical row inventory/provenance; many owner fields are not canonical-account FKs; no complete source/migrated/quarantined key reconciliation. |
| 3G.9 SQLite runtime/durability | **PARTIALLY PROVEN** | Source connection arguments, per-store transactions, selected PRAGMAs, file permissions, and repository tests are inspectable. | No supported SQLite version/profile, effective journal/synchronous readings, filesystem qualification, multi-process/power-loss/fault-injection results, or SQLite backup/restore evidence. |
| 3G.10 Recovery/cutover | **NOT PROVEN** | The migration design specifies sensible fail-closed requirements; several existing per-store migrations fail closed on unsupported/corrupt schemas. | No shared migration tool, `RECOVERY_REQUIRED` state, authority/path manifest gate, global quiescence, stale-binary exclusion, database restore procedure, or tested cutover/rollback/forward-recovery. |

## 3G.7 — Historical HMAC keys

- **Source-confirmed current behavior:** Session/evidence HMAC-SHA-256 uses UTF-8 bytes from `PARMAR_SESSION_STORE_KEY` directly (`SQLiteSessionRepository.from_environment`). Session tags have `PARMAR-SESSION-RECORD-v1:` through `v4:` formats; evidence uses `PARMAR-IDENTITY-EVIDENCE-v1:`. Session schema versions 1–4 do not record a key ID, and schema v4 can contain earlier record formats.
- Approval integrity uses `PARMAR_APPROVAL_STORE_KEY` or the session-key fallback, derives an HMAC key with the non-secret domain label `PARMAR-APPROVAL-STORE-v1\0`, then signs the current token-hash/state/expiry/payload tuple. Approval schema v1–v3 has no per-row key or format identifier. Cancellation audit is nested in the current signed payload, not a separate append-only record.
- A transient CSRF HMAC uses an injected or process-random `csrf_secret`; it is not a persisted source record. No other persistent HMAC-protected record implementation was found.
- Tests named in [tests/test_sessions.py](./tests/test_sessions.py) and [tests/test_approval_persistence.py](./tests/test_approval_persistence.py) cover selected schema migrations, tampering, lifecycle, restart, and same-process concurrency. No tests were run; no source evidence covers historical key rotation/recovery or proves any deployed key version.
- **Unresolved risk/action:** Assume no historical key can be recovered until authoritative secret-management/deployment evidence maps each immutable source backup and record era to the original key. Unverified or ambiguous records must be quarantined, never re-signed into trusted status.
- Detailed evidence: [PARMAR-HMAC-KEY-CONTINUITY-REPORT.md](./PARMAR-HMAC-KEY-CONTINUITY-REPORT.md).

## 3G.8 — Owner/account mapping

- Five independent PARMAR SQLite path settings separate identity registry, sessions/evidence, approvals, conversations, and memory/consent. Their schema versions are identity v3, sessions v4, approvals v3, conversations v3, and memories v1. Configured paths and actual database versions/rows were not read.
- `accounts.account_id` is constrained UUIDv4; provider bindings have an exact `(issuer, subject)` key and account FK. Profiles/workspaces/members/grantors and memberships store user identifiers without canonical account FKs. Session/evidence rows contain explicit account identity only in newer schema/format eras and require HMAC verification. Approval payloads bind user/session/action; conversation and memory rows use owner user IDs. Messages/research inherit conversation scope through their parent FKs.
- Identity/conversation relationships have some SQL FK/schema validation. Session/evidence, approval/session/account, and memory/consent ownership rely on application checks or owner predicates; no live data was checked for orphans, conflicts, or matching canonical records.
- `README.md` says production trusted identity/provider integration is not implemented. UUID/attribute equality is not provenance. JSONL decision logs have arbitrary payloads; browser-local demo records are not authenticated account data. AETHER’s app-private SQLite store is separate and has no account/owner fields.
- **Unresolved risk/action:** Per-row account mapping, orphan/conflict totals, and full reconciliation are NOT PROVEN. Use an independently reviewed manifest keyed by source DB/schema/table/primary key; require independent provenance; assign `UNMAPPED`, `QUARANTINED`, or `UNVERIFIABLE` where proof is absent; preserve NULL profile scope; reconcile every source key into exactly one migrated/quarantined set.
- Detailed evidence: [PARMAR-OWNER-MAPPING-EVIDENCE-REPORT.md](./PARMAR-OWNER-MAPPING-EVIDENCE-REPORT.md).

## 3G.9 — SQLite runtime and durability

- All five Python repositories open per-operation connections using `timeout=5` and `isolation_level=None`, close them, and use per-object `threading.RLock`s. Write methods generally use `BEGIN IMMEDIATE`; memory reads begin a read transaction. Identity explicitly enables and reads back `foreign_keys=ON` and sets `busy_timeout=5000`; conversations enables foreign keys but does not read the setting back. The other three do not enable FK enforcement in their connection setup.
- No Python store sets/verifies journal mode or synchronous level. The project does not define a supported SQLite version range; `requirements.txt` contains only pytest. Default database paths are under the user’s local data directory, but environment overrides are not filesystem-qualified.
- Repository tests cover selected fresh-schema/migration, reopen, corruption, ownership, and thread/connection-concurrency cases. These test sources were not run and do not establish separate-process behavior, power-loss durability, commit-boundary crash recovery, disk-full/read-only behavior, supported filesystem semantics, or a consistent database backup/restore.
- The backup tool explicitly excludes SQLite files; its runbook is a worktree archive with a dry-run restore plan.
- **Unresolved risk/action:** Effective runtime settings and storage environment are NOT PROVEN. Define an exact SQLite/runtime/filesystem/process profile; capture and validate effective settings at startup; add SQLite online backup/restore and fault-injection tests on each claimed platform.
- Detailed evidence: [PARMAR-SQLITE-DURABILITY-EVIDENCE-REPORT.md](./PARMAR-SQLITE-DURABILITY-EVIDENCE-REPORT.md).

## 3G.10 — Recovery and cutover

- `RECOVERY_REQUIRED` appears in the design, not in [interface/approvals.py](./interface/approvals.py), its `ApprovalState`, transitions, schema constraints, or tests. Current `EXECUTING` state persists separately from provider/effect activity and may transition to `CANCELLED`, `CONSUMED`, or `EXPIRED`; no terminal indeterminate disposition is implemented.
- Existing schema upgrades are per-store initializers. There is no shared `migration_manifest`, authority ID, expected database-set validation, startup check for an incomplete shared migration, global write drain/quiescence, old-binary fence, or service-account write revocation.
- `docs/backup-recovery.md` and `scripts/parmar_backup.py` cover worktree files, not SQLite snapshots; no restore-write command or tested forward recovery exists. Cutover/rollback restrictions are design requirements only.
- **Unresolved risk/action:** Recovery/cutover is NOT PROVEN. Implement no migration now. Before any authorized future prototype, implement terminal/non-executable recovery state semantics, offline mapping/reconciliation, consistent database backup/restore, quiescence and stale-writer exclusion, startup identity/path validation, and interrupted-cutover tests.
- Detailed evidence: [PARMAR-RECOVERY-CUTOVER-READINESS-REPORT.md](./PARMAR-RECOVERY-CUTOVER-READINESS-REPORT.md).

## Cross-phase consistency

- **Phase 3G.1 NO-GO:** [safety/runtime_no_go.py](./safety/runtime_no_go.py), `require_atomic_authorization_fence`, unconditionally denies enumerated unfenced operations. Current chat service and HTTP call sites deny authenticated/approved chat, memory mutation, profile/workspace writes, and session profile selection. Production startup remains disabled for unverified identity in [interface/futuristic_app.py](./interface/futuristic_app.py); README documents local development auth as unverified. Test source asserts denials, but tests were not run.
- **Direct writes/shared boundary:** Current runtime has separate writable repositories and public write methods; no shared authority transaction exists. Checked external route/service paths invoke NO-GO before protected effects, but the repositories themselves are not a universal fence. Every direct service/repository/background path must be inventoried and made incapable of bypass before any future protected effect can be enabled.
- **Proposed transaction scope:** The design lists accounts/bindings, sessions/evidence, generations, approvals, consent/effect, and an audit outbox. This is an unimplemented proposal. It still needs an API/caller inventory proving every mutable predicate for the selected operation is included and all legacy direct writes are blocked.
- **AETHER boundaries:** No AETHER files were modified in this work. Desktop security docs keep remote browsing NO-GO and `REMOTE_BROWSING_ENABLED=false`; Android has no Internet permission and uses inert local URL metadata. No authenticated production or AETHER remote-browsing capability was enabled by this audit. No runtime/device test was run.

## Overall decision

# NOT READY — critical prerequisites remain unproven.

### Evidence classifications and readiness levels

Evidence classifications describe only the claim and scope examined:

- **PROVEN:** implementation plus relevant reproducible evidence supports the
  stated claim within its defined scope.
- **PARTIALLY PROVEN:** evidence supports only a bounded part of the claim;
  material prerequisites or environment-dependent behavior remain unverified.
- **NOT PROVEN:** the reviewed evidence does not establish the claim. This does
  not assert that the claim is false.

Readiness levels are decision labels, not approvals:

1. **Evidence reviewed / NOT READY:** static evidence has been assessed, but one
   or more critical prerequisites remain NOT PROVEN or PARTIALLY PROVEN. No
   prototype or production operation is authorized by this level. **This
   assessment is Level 1.**
2. **Synthetic-only experiment eligible for authorization:** the isolation
   requirements in
   [PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md](./PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md)
   have been independently verified for a separate disposable environment.
   This level still requires explicit recorded human authorization before each
   run and does not authorize real data, production identity, or protected
   runtime effects.
3. **Production-migration readiness eligible for separate approval:** every
   relevant historical key, owner mapping, durability, and recovery/cutover
   prerequisite is PROVEN with reproducible evidence, and the required
   independent reviews are complete. This level is not production release or
   execution approval; those require separate authorization.

The current assessment remains **Level 1**. The Level 2 isolation criteria are
specified but have not been independently demonstrated; Level 2 is therefore
not established. Level 3 is **NOT PROVEN**. The prior phrase “READY FOR
INDEPENDENT REVIEW” is not a readiness level in this rubric and must not be
read as permission to begin an integrated prototype. **PRODUCTION MIGRATION
READY** is not supported.

An offline synthetic-only experiment, if it ever reaches Level 2, would not
close the historical key, ownership, runtime durability, or recovery gates.
It is not authorized merely by the documentation or the readiness label.

### Critical blockers and required next action

1. **Key continuity:** recover authoritative history and verify each immutable source record under its original key/format; quarantine failures.
2. **Ownership:** establish independent canonical-account provenance and reconcile every owner-bearing source key, with explicit quarantine for unknown/ambiguous rows.
3. **Durability:** select a supported runtime/storage profile and demonstrate effective SQLite settings, independent-process behavior, failure handling, and database backup/restore.
4. **Recovery/cutover:** before real-data or production-runtime prototyping or cutover, implement and independently test a terminal non-executable `RECOVERY_REQUIRED` state, all-writer quiescence, old-writer exclusion, authority/path manifest checks, and crash/rollback/forward-recovery procedures.
5. **NO-GO:** preserve the unconditional Phase 3G.1 gate throughout; no route, provider, or protected write is eligible from this evidence.

**Single safest next action:** Keep all runtime NO-GO gates unchanged. Before any synthetic-only experiment is executed, independently verify every isolation requirement in the migration design and obtain explicit recorded human authorization. For migration readiness, separately perform a read-only provenance preflight using quiesced immutable database backups plus controlled key-custody history; produce per-record verified/mappable, quarantined, or unverifiable dispositions before any real-data or production-runtime prototype is considered.

## Validation boundary

Reports were assembled from source and test-name inspection only. No tests, migrations, live database reads, runtime probes, filesystem durability tests, or secret-manager queries were performed. Those gaps are explicitly NOT PROVEN; documentation and test names are not test execution or production evidence.
