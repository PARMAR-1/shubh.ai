# PARMAR Historical Owner and Account Mapping Evidence Report

**Scope and handling:** Static inspection of source schemas, repository methods, tests, docs, and backup tooling. No live database was opened, queried, backed up, or modified. Consequently, this report inventories schema/source semantics; it does not claim historical row counts, actual orphan counts, or a completed source-to-destination reconciliation.

## Executive finding

**Historical owner-to-account mapping: NOT PROVEN.** Canonical account UUIDs and provider-binding schema exist, but most owner-bearing rows store user UUID strings without foreign keys to the account registry. Production identity-provider integration and trusted account resolution are not implemented. Existing row-level mappings, conflicts, and orphans cannot be determined without controlled database snapshots and authoritative provenance.

## Source database and table inventory

Paths below are the runtime configuration names/defaults documented in [README.md](./README.md); current configured paths and actual existing schema versions were not read.

| Source database / schema | Table and primary key | Owner/account fields and relationships |
|---|---|---|
| `PARMAR_IDENTITY_DB_PATH`, default `~/.local/share/parmar/identity.sqlite3`; `SQLiteIdentityRepository` v3 ([identity_store.py](./identity_store.py)) | `accounts(account_id)` | Canonical account ID constrained to UUIDv4; status and created time. Triggers prohibit account-ID changes and account deletion. |
| same identity DB, v3 | `provider_account_bindings(issuer, subject)` | Exact issuer/subject/account tuple; `account_id` foreign-keys to `accounts` with delete restricted. This is the clearest schema-level account binding, but no historical rows or provider proof were examined. |
| same identity DB, base v1 plus registry migrations | `profiles(profile_id)` | `owner_user_id` is a text UUID-like value, not a foreign key to `accounts`. |
| same identity DB | `workspaces(workspace_id)` | `owner_user_id` is not a foreign key to `accounts`. |
| same identity DB | `workspace_members(workspace_id, user_id)` | Workspace FK with cascade; `user_id` is not an account FK. Role is constrained to owner/admin/member. |
| same identity DB | `workspace_profiles(workspace_id, profile_id)` | Workspace/profile FKs with cascade; `granted_by_user_id` is not an account FK. This is the share/grant relationship. |
| `PARMAR_SESSION_DB_PATH`, default `~/.local/share/parmar/sessions.sqlite3`; `SQLiteSessionRepository` v4 ([sessions.py](./sessions.py)) | `sessions(session_id)`; `token_hash` is also unique | `user_id`, optional `account_identity`, optional `active_profile_id`, optional `identity_evidence_id`; no schema FK to account/evidence. The application verifies signed record fields and trusted identity relationships. |
| same session DB, introduced in session schema v4 | `identity_evidence(evidence_id)` | `account_identity` and `session_id`; `session_id` is not an SQL FK. Application-level checks bind evidence to a session and account; both records’ HMACs must be verified before trusting historical relationships. |
| `PARMAR_APPROVAL_DB_PATH`, default `~/.local/share/parmar/approvals.sqlite3`; `SQLitePendingApprovalStore` v3 ([interface/approvals.py](./interface/approvals.py)) | `pending_approvals(token_hash)` | Signed payload includes `user_id`, `session_id`, and action binding `actor_id`; cancellation audit is in the payload. No SQL FK to session/account. `_record_matches_owner`, binding checks, and session revalidation enforce current repository operations, not a historical account-provenance record. |
| `PARMAR_CONVERSATION_DB_PATH`, default `~/.local/share/parmar/conversations.sqlite3`; `SQLiteConversationRepository` v3 ([conversations.py](./conversations.py)) | `conversations(conversation_id)` | `owner_user_id`; nullable `profile_id`. Neither field is an account FK; profile ID is not a declared FK to `profiles`. |
| same conversation DB | `messages(message_id)` | Parent `conversation_id` FK cascades from conversations; ownership derives through the parent. |
| same conversation DB | `message_research(message_id)` | FKs to the exact message/conversation relationship and conversation; research rows inherit parent scope. They do not store a separate account field. |
| `PARMAR_MEMORY_DB_PATH`, default `~/.local/share/parmar/memories.sqlite3`; `SQLiteMemoryRepository` v1 ([memories.py](./memories.py)) | `memory_consents(owner_user_id)` | Per-user consent key; no account FK. |
| same memory DB | `memories(memory_id)` | `owner_user_id`; no account FK. |
| JSONL at the configured/default path ([audit/decision_logs.py](./audit/decision_logs.py), `DecisionLogManager.log_event`) | No fixed table, primary key, or schema | Arbitrary sanitized event payloads may retain caller-supplied identity/owner fields; the code does not define a complete inventory or sign the records. Any migration of this log requires a separate content/key review. |

### Other local stores and explicit exclusions

- [interface/static/app.js](./interface/static/app.js) and [README.md](./README.md) identify browser-local demo memories and local-demo sessions. They are not authenticated account data and have no canonical account mapping; do not import or merge them into the shared authority.
- [android/app/src/main/kotlin/org/parmar/aether/data/LocalAetherDatabase.kt](./android/app/src/main/kotlin/org/parmar/aether/data/LocalAetherDatabase.kt) has app-private `bookmarks`, `tabs`, and `metadata` tables, database version 1, and no owner/account fields. [android/PERSISTENCE-ARCHITECTURE.md](./android/PERSISTENCE-ARCHITECTURE.md) states that there is no shared PARMAR backend. It is not an owner-mapping source for this design.
- Desktop/AETHER files are not identified by the migration design as authorization stores. The AETHER offline/security boundaries remain separate; no cross-product account mapping is established.

## Ownership semantics and provenance

- `AccountIdentity` is represented explicitly in newer trusted session/evidence records. `ServerSession` validates its user ID against `account_identity.value`, and the session/evidence repository checks the relationship. This is potentially useful provenance only after original HMAC verification, lifecycle validation, and exact account-registry reconciliation.
- The identity registry can represent an exact provider issuer/subject/account binding, but [README.md](./README.md) states that production identity-provider integration and trusted account resolution are not implemented. No authoritative historical inventory of provider bindings or events was found.
- Legacy profiles/workspaces/memberships, approvals, conversations, and memories predominantly use `user_id`/`owner_user_id` strings. Equality with a canonical account UUID, or agreement with an email, name, profile label, or timestamp, is not proof.
- The development login is a fixed, unverified local principal. Its IDs cannot establish distinct person/account ownership for historical records.

## Relationship and migration evidence

- **Database constraints:** identity bindings reference accounts; workspace members reference workspaces; workspace-profile grants reference workspaces and profiles; conversation messages/research have parent FKs. Profile/workspace owner IDs and grantors, session/evidence/account associations, approval/session associations, conversation owners, and memory/consent owners do not all have canonical-account FKs.
- **Repository checks:** identity and conversation code validate selected schema/FK invariants; session/approval code verifies HMAC and application-level associations; memory ownership checks are query predicates. These are implementation rules, not evidence that every historical row currently satisfies them.
- **Schema migrations:** identity v1/v2→v3 creates or migrates account/binding tables while preserving legacy profiles without assigning accounts; sessions v1→v4 adds identity/evidence fields and revokes active trusted sessions lacking evidence lifecycle records; approvals v1/v2→v3 updates lifecycle constraints; conversations v1→v3 adds research/profile scope; memory supports fresh v1 only.
- **Tests (source names inspected, not run):** `tests/test_account_registry.py::test_registry_migration_preserves_legacy_profiles_without_assigning_accounts`, account/binding validation and migration-atomicity tests; `tests/test_sessions.py` v1/v2/v3 migration and evidence tests; `tests/test_approval_integrity.py` owner/session/binding and unbound legacy approval tests; `tests/test_conversations.py` ownership/profile/migration tests; `tests/test_memories.py` owner-isolation and corruption tests. These exercise seeded test data, not live historical records or provenance.
- **Backup tooling:** [scripts/parmar_backup.py](../scripts/parmar_backup.py) explicitly excludes `.db`, `.sqlite`, and `.sqlite3` suffixes. [docs/backup-recovery.md](../docs/backup-recovery.md) documents a worktree archive and dry-run restore plan, not a database snapshot. It cannot provide the row inventory required here.

## Unmapped, ambiguous, orphaned, and conflicting records

No row-level database inspection was permitted or performed. Therefore the following remain **unknown**, not absent: actual owners; account-to-user correspondence; dangling owner IDs; duplicate/conflicting bindings; cross-account profile/share relationships; orphaned session/evidence pairs; stale or mismatched approval owners; message/research parent errors; consent/memory owner mismatches; and row counts per schema era.

Until independent provenance is recorded, quarantine and deny authorization for:

- Sessions lacking a verified canonical account/evidence association, including legacy schemas without account identity.
- Identity evidence whose source HMAC, owning session, account, lifecycle, or interval cannot be verified.
- Approvals whose owner/session/action binding cannot be tied to verified source evidence; all ambiguous in-flight records require non-executable treatment.
- Profiles, workspaces, member/grantor/share rows whose user IDs cannot be mapped independently.
- Conversation and memory/consent rows with ambiguous owners. Preserve nullable legacy profile scope as NULL; never assign an active account/profile by guess.
- Any arbitrary audit JSONL record whose owner fields or provenance are unresolved. Keep browser-local demo data outside the authenticated migration.

## Required mapping manifest and reconciliation

For each owner-bearing source row, maintain a separately access-controlled manifest keyed by `(source database identifier, schema version, table, primary key)`. Record:

- Source row digest and original owner value/namespace in the restricted manifest (do not put personal identifiers in this report).
- Target canonical account UUID, or explicit `UNMAPPED`, `QUARANTINED`, or `UNVERIFIABLE` disposition.
- Independent provenance reference and verification result; reviewer and decision time.
- Related source primary keys and validated FK/application-level relationship results.

Reconcile each table’s actual source primary-key set against disjoint migrated and quarantined sets, with no omission, overlap, or unexplained extra row. Compare stable row digests, counts, FK checks, and owner relationships. No live row sets or digests are available now, so completeness is **NOT PROVEN**. Missing provenance, inconsistent links, conflicting mappings, invalid HMACs, and unsupported schema/format eras must remain quarantined and non-authorizing.

**Conclusion:** The source schema inventory and repository-level ownership semantics are **partially evidenced**. Historical ownership, actual orphan/conflict counts, and complete source-to-destination reconciliation are **NOT PROVEN**. No record may be mapped by UUID or attribute matching alone.
