# PARMAR Shared Authority Migration Feasibility Evidence Report

**Audit scope:** static, read-only inspection of the migration design and current PARMAR source, schema initialization, configuration documentation, tests, and backup/recovery documentation. No runtime, database, migration, or environment-secret values were read. No tests or migrations were run.

## 1. Historical HMAC key continuity — NOT PROVEN

**Evidence and formats**

- [sessions.py](./sessions.py), `SQLiteSessionRepository.from_environment`, `_session_integrity_tag`, and `_identity_evidence_integrity_tag`: sessions use the UTF-8 bytes of `PARMAR_SESSION_STORE_KEY` directly (minimum 32 bytes). HMAC-SHA-256 tags are 64-character lowercase hex. Session payloads are compact ASCII JSON arrays with domain prefixes `PARMAR-SESSION-RECORD-v1` through `v4`, selected by row fields; identity-evidence tags use `PARMAR-IDENTITY-EVIDENCE-v1`. Evidence and sessions use the same configured session key.
- Session schema `user_version` 1→2 adds `active_profile_id`; 2→3 adds account identity and authentication times; 3→4 adds `identity_evidence_id` and the evidence table. Thus v4 schema rows can still use earlier session tag formats; the row does not store a tag-format/key identifier.
- [interface/approvals.py](./interface/approvals.py), `SQLitePendingApprovalStore.from_environment` and `_tag`: the source is `PARMAR_APPROVAL_STORE_KEY`, falling back to `PARMAR_SESSION_STORE_KEY`; the configured string is domain-separated and SHA-256-derived into the approval HMAC key. HMAC-SHA-256 covers the UTF-8, NUL-delimited token hash, state, expiry, and stored payload; the tag is lowercase hex. Approval `user_version` 1→2→3 migrations validate and copy tags. Neither a per-row key ID nor tag-format version is stored.
- [PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md](./PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md) §2 and §5 describes these stores and itself notes the absence of persisted historical key IDs/key rings.

**Missing evidence:** Repository code and documentation identify current key inputs and algorithms, but do not establish which historical key values were deployed, whether they remain recoverable, when they changed, or a mapping from each database/row/schema era to the correct key. The current environment and any secret-management history were not inspected. A valid tag format or a matching user/account value cannot establish key continuity.

**Before implementation:** Obtain controlled, non-logged key custody/rotation evidence and a reviewed mapping of each source database and row-format era to its original key. Verify every source row against that inventory and representative fixtures without exposing key material. Any unavailable, ambiguous, or unverifiable source key must block activation; affected source records cannot be silently re-signed.

## 2. Legacy owner/account mapping — NOT PROVEN

**Owner/account references found in the design’s SQLite sources**

- [identity_store.py](./identity_store.py), schema v3: `accounts.account_id`; `provider_account_bindings(issuer, subject, account_id)`; `profiles.owner_user_id`; `workspaces.owner_user_id`; `workspace_members.user_id`; and `workspace_profiles.granted_by_user_id`. Account IDs are constrained UUIDv4; bindings reference accounts and account IDs are immutable. The profile/workspace owner and member fields are not canonical-account foreign keys.
- [sessions.py](./sessions.py), schema v4: `sessions.user_id`, optional `account_identity`, `active_profile_id`, and `identity_evidence_id`; `identity_evidence.account_identity` and `session_id`. The runtime validates identity/session relationships, but historical verification depends on the HMAC continuity above. Older sessions may have no account identity or evidence association; the v3→v4 migration revokes active trusted sessions lacking evidence lifecycle rows.
- [interface/approvals.py](./interface/approvals.py), schema v3: owner and session are in the signed payload (`user_id`, `session_id`), with audit payload fields such as `actor_user_id`; the table itself is keyed by token hash.
- [conversations.py](./conversations.py), schema v3: `conversations.owner_user_id` and nullable `profile_id`; messages and research metadata inherit scope through conversation/message relationships. v1→v2 adds research metadata and v2→v3 adds profile scope.
- [memories.py](./memories.py), schema v1: `memory_consents.owner_user_id` and `memories.owner_user_id`. No prior schema migration is implemented.
- [audit/decision_logs.py](./audit/decision_logs.py), `DecisionLogManager.log_event`: JSONL records have arbitrary sanitized payloads, not a fixed owner/account schema; they require a separate inventory before any audit-log migration.

There is limited potentially useful provenance: explicit account identities in session/evidence rows and exact provider issuer/subject/account bindings. They are usable only if the source integrity is verified and the binding/account relationships are independently established. The [README.md](./README.md) states that production identity-provider integration and trusted account resolution are not implemented; its development principal is fixed and explicitly unverified. The repository does not establish a historical binding population or provenance that joins all legacy `user_id`/`owner_user_id` values to canonical accounts. Matching UUIDs, names, email-like attributes, or labels is not proof.

**Records requiring quarantine unless independent provenance is supplied:** legacy sessions with no verified canonical identity/evidence; owner/member/grantor rows in profiles, workspaces, memberships, and shares; approval payloads whose owner cannot be linked through verified session evidence; conversation and memory/consent rows whose owner cannot be mapped; and any JSONL audit records selected for migration whose provenance/schema is unresolved. Preserve NULL conversation profile scope as NULL. Exact canonical account and provider-binding rows may be preserved after integrity and relationship checks, but they do not prove unrelated owner fields.

**Before implementation:** Produce an attributable, independently reviewed mapping manifest keyed by source database, table, and primary key. Include a canonical account or explicit `UNMAPPED`/`QUARANTINED` disposition and independent provenance for every owner-bearing record. Reconcile all source keys and relationships into migrated or quarantined sets; do not activate while any record is omitted, guessed, or unreconciled.

## 3. SQLite runtime and durability — PARTIALLY PROVEN

**Repository evidence**

- The five repositories select separate local database paths via `PARMAR_SESSION_DB_PATH`, `PARMAR_IDENTITY_DB_PATH`, `PARMAR_APPROVAL_DB_PATH`, `PARMAR_CONVERSATION_DB_PATH`, and `PARMAR_MEMORY_DB_PATH`, each with a separate `~/.local/share/parmar/*.sqlite3` default ([README.md](./README.md); repository `from_environment` methods).
- Their connection methods open per-operation connections using `sqlite3.connect(..., timeout=5, isolation_level=None)`, then close them. Write paths generally use `BEGIN IMMEDIATE`; memory reads use `BEGIN`. Each repository has an in-process `threading.RLock`, which does not coordinate repository instances or processes.
- Identity and conversation connections enable `PRAGMA foreign_keys=ON`; identity verifies the setting, and conversations validate foreign keys/schema. Sessions, approvals, and memories do not enable foreign keys in their connection setup; their schemas do not establish comparable cross-store constraints. Identity sets `PRAGMA busy_timeout=5000`; `timeout=5` is also passed to the other SQLite connections.
- No inspected repository sets or verifies `PRAGMA journal_mode` or `PRAGMA synchronous`. Effective settings are therefore not evidenced. The repositories create parent directories with mode 0700, reject database symlinks, and chmod database files to 0600. These permissions do not prove filesystem durability or a supported filesystem class.
- Existing schema upgrades run from each repository’s `_initialize` path, independently. Relevant versions are sessions v1–v4, identity v1–v3, approvals v1–v3, conversations v1–v3, and memories v1 only.

**Environment-dependent / not proven:** Actual SQLite library/version and compile options, effective journal and synchronous modes, configured paths, filesystem and mount semantics, storage hardware, simultaneous process inventory, power-loss behavior, and multi-process writer behavior were not measured. The tests contain repository/schema and concurrency coverage, but no inspected evidence establishes power-loss durability or supported multi-process cutover behavior. [docs/backup-recovery.md](../docs/backup-recovery.md) validates a Linux worktree-archive workflow, not SQLite durability or database backup/restore.

**Before implementation:** Specify and enforce an approved SQLite/version, journal/synchronous, timeout, foreign-key, filesystem, path, and process profile. Test the actual supported filesystems and independent processes under contention, termination at commit boundaries, disk-full/read-only/corruption conditions, and backup/restore. Do not claim power-loss, multi-process, or cross-database durability without those results.

## 4. Recovery and cutover readiness — NOT PROVEN

**Implemented:** [safety/runtime_no_go.py](./safety/runtime_no_go.py), `require_atomic_authorization_fence`, unconditionally raises `RuntimeNoGo` for the listed unfenced operations. This is a separate Phase 3G.1 runtime denial; this audit makes no change to it.

**Specified, not implemented:** `RECOVERY_REQUIRED` appears in the design proposal, but not in runtime/schema code. `ApprovalState` and the v3 `pending_approvals` state constraint in [interface/approvals.py](./interface/approvals.py) do not include it. There is no demonstrated terminal, non-executable recovery state for migrated `EXECUTING` approvals. The design’s §6–§7 procedures, migration manifest, source-to-destination reconciliation, authority ID check, single switch, old-process exclusion, and forward-recovery rules remain requirements rather than implemented controls.

Existing repository migrations are independent startup schema upgrades, not a cross-database authority migration. The `PARMAR_*_DB_PATH` settings independently select stores; per-repository file-identity checks do not prevent an old binary or mixed set of database paths from being used. No shared-authority startup identity check, migration mode, all-writer shutdown/freeze control, or old-binary exclusion was evidenced.

[docs/backup-recovery.md](../docs/backup-recovery.md) covers worktree files and patches; its restore is dry-run only and it expressly has no restore-write command. It does not provide a consistent SQLite online backup/restore procedure. The design’s proposed SQLite backup, cutover, and recovery procedures have not been demonstrated by repository implementation or test evidence.

**Before implementation:** Define and test the terminal recovery-state schema and prove every inspect/authorize/claim/consume path denies it. Implement an offline, fail-closed migration and reconciliation tool; verified SQLite backups and tested restore; exclusive shutdown/freeze and old-writer exclusion; startup validation against one authority/database set; and pre/post-activation recovery rules. Keep the existing runtime NO-GO gate unconditional unless separately reviewed and approved.

## Overall decision: NOT FEASIBLE YET

The design is a proposal, not migration evidence. Historical key continuity and canonical owner mapping are not proven; SQLite durability and process assumptions remain unqualified; and the recovery/cutover controls, including terminal `RECOVERY_REQUIRED`, are not implemented. Do not begin shared-authority implementation or cutover until the prerequisites in all four sections are met.
