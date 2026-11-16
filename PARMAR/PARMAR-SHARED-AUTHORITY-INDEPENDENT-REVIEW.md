# PARMAR Shared Authority Readiness — Independent Review (Phase 3G.11)

## Scope and method

This is a read-only review of the four Phase 3G.7–3G.10 evidence reports and the consolidated readiness assessment. Claims were checked against the current PARMAR source, relevant test definitions, and existing documentation. No tests, migrations, runtime probes, database reads, or secret reads were performed. Only this review report is written.

## Overall verdict

**Substantially supported, with corrections needed for decision clarity.** The reports accurately distinguish current implementation facts from historical/environmental facts that are not established. The core readiness classifications are justified: historical HMAC continuity, historical owner mapping, and recovery/cutover are **NOT PROVEN**; SQLite behavior is **PARTIALLY PROVEN** at source-configuration level, while effective durability remains **NOT PROVEN**. No important claim reviewed establishes production authentication, production migration, or protected execution as approved.

There are no findings that justify changing those classifications upward. Keep production and integrated-prototype decisions **NO-GO**.

## Findings, ranked by severity

### 1. Prototype scope is not operationally bounded, and reports use “prototype” inconsistently

**Severity: MEDIUM**

**Evidence:** The consolidated assessment says a “wholly isolated synthetic-only harness” may be proposed and approved, but gives no isolation/acceptance criteria. The recovery/cutover report says its full migration prerequisites apply “before any runtime prototype,” without distinguishing a synthetic harness from a prototype touching source stores or protected runtime paths. The migration design is explicitly proposal-only; no migration implementation or authority-ID gate was found.

**Source checked:** [PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md](./PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md), overall decision; [PARMAR-RECOVERY-CUTOVER-READINESS-REPORT.md](./PARMAR-RECOVERY-CUTOVER-READINESS-REPORT.md), required readiness gates; [PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md](./PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md), status and migration requirements.

**Required correction:** Define separate categories. No prototype may read or mutate real source databases, use production secrets, authorize real identities, call providers, or enable protected effects until the relevant migration gates are met. A synthetic-only harness could be considered separately only with generated fixtures/keys, isolated storage and explicit non-production paths, no network/provider access, no migration writes or production-code wiring, unchanged runtime NO-GO guards, and an independently reviewed isolation test/approval. State that this is a conditional future option, not current authorization.

### 2. “Readiness level 1” is unsupported by a cited scale

**Severity: LOW**

**Evidence:** The readiness assessment states, “This corresponds to readiness level 1,” but none of the five reviewed reports defines a numbered scale or a mapping from evidence to level 1.

**Source checked:** [PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md](./PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md), overall decision.

**Required correction:** Cite the governing rubric and its applicable criterion, or remove the numeric level. The qualitative decision **NOT READY** is independently supported by the report’s own listed blockers.

### 3. HMAC evidence report omits the exact approval-key derivation label

**Severity: LOW**

**Evidence:** The HMAC report says the approval key is SHA-256 of the UTF-8 secret “preceded by the domain-separation label” but does not state the label. The consolidated assessment does provide it. Source `SQLitePendingApprovalStore.from_environment` derives the key as `sha256(b"PARMAR-APPROVAL-STORE-v1\0" + secret.encode("utf-8"))`; `_tag` encodes its message as UTF-8.

**Source checked:** [interface/approvals.py](./interface/approvals.py), `SQLitePendingApprovalStore.from_environment` and `_tag`; [PARMAR-HMAC-KEY-CONTINUITY-REPORT.md](./PARMAR-HMAC-KEY-CONTINUITY-REPORT.md), approval integrity section; [PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md](./PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md), 3G.7 section.

**Required correction:** If the individual report is revised, record the exact non-secret label and encodings so its algorithm description is reproducible. This omission does not change the historical continuity finding.

## Claim and classification verification

### 3G.7 — HMAC key continuity

- **Verified source claims:** `SQLiteSessionRepository.from_environment`, `_session_integrity_tag`, and `_identity_evidence_integrity_tag` in [sessions.py](./sessions.py) confirm the session/evidence key source, HMAC-SHA-256, current tag formats, and lack of a persisted key ID. `SQLitePendingApprovalStore.from_environment` and `_tag` in [interface/approvals.py](./interface/approvals.py) confirm approval-key fallback/derivation and signed fields. Approval cancellation is included in the current signed payload, not a separate append-only authenticated log.
- **Classification:** **NOT PROVEN** for historical key custody, rotation history, and mapping an extant row to its original key. Current code proves only present algorithms/configuration; it cannot establish which deployed secret was used historically. The report correctly does not infer a key from schema/tag version.

### 3G.8 — Owner/account mapping

- **Verified source claims:** The named schemas and owner fields are consistent with `SQLiteIdentityRepository` in [identity_store.py](./identity_store.py), `SQLiteSessionRepository` in [sessions.py](./sessions.py), `SQLitePendingApprovalStore` in [interface/approvals.py](./interface/approvals.py), `SQLiteConversationRepository` in [conversations.py](./conversations.py), and `SQLiteMemoryRepository` in [memories.py](./memories.py). `DecisionLogManager.log_event` in [audit/decision_logs.py](./audit/decision_logs.py) accepts sanitized but not fixed-schema payloads, so owner-like fields cannot be ruled out without a separate inventory. Browser-local state is not authenticated source data. The separate app-private AETHER database in [LocalAetherDatabase.kt](./android/app/src/main/kotlin/org/parmar/aether/data/LocalAetherDatabase.kt) is documented as offline in [PERSISTENCE-ARCHITECTURE.md](./android/PERSISTENCE-ARCHITECTURE.md) and is not a PARMAR account source.
- **Classification:** **NOT PROVEN** for historical row mappings, orphan/conflict counts, or complete reconciliation. Current schema relations and tests on seeded fixtures do not prove historical provenance. The quarantine/unmapped treatment is justified.

### 3G.9 — SQLite behavior and durability

- **Verified source claims:** The five repositories use per-operation SQLite connections and `timeout=5`; identity verifies `foreign_keys=ON` and sets `busy_timeout=5000`, while conversations enables foreign keys without reading the setting back. The inspected connection code does not set/verify journal mode or synchronous level. Per-object `RLock`s and selected `BEGIN IMMEDIATE` transactions do not establish multi-process, filesystem, or power-loss guarantees. `SQLiteIdentityRepository.create_profile` is a single autocommit insert; the report correctly distinguishes it from multi-statement identity writes.
- **Test evidence limit:** The cited test files contain restart, migration, corruption, and selected thread/connection concurrency cases. Test names/source are not execution results; the reports disclose that tests were not run. No reviewed evidence proves SQLite backup/restore, process-kill recovery, or supported deployment filesystem behavior.
- **Classification:** **PARTIALLY PROVEN** is justified only for inspectable source configuration and repository behavior. Effective runtime settings and durability are **NOT PROVEN**.

### 3G.10 — Recovery and cutover

- **Verified source claims:** `ApprovalState`, `_APPROVAL_TRANSITIONS`, and the `pending_approvals` schema in [interface/approvals.py](./interface/approvals.py) include `EXECUTING` but not `RECOVERY_REQUIRED`; existing schema upgrades are per-store. [scripts/parmar_backup.py](../scripts/parmar_backup.py) excludes SQLite suffixes, and [docs/backup-recovery.md](../docs/backup-recovery.md) specifies a dry-run plan, not a database restore-write path. The shared-authority manifest, all-writer quiescence, old-binary exclusion, authority-ID startup gate, rollback boundary, and forward recovery are design requirements rather than implemented controls.
- **Classification:** **NOT PROVEN** is justified for migration, recovery, rollback, and cutover. The reports correctly distinguish those from existing per-store schema migrations and do not claim an operational migration or restore path.

## Phase 3G.1 NO-GO and approval boundary

The inspected Phase 3G.1 gate remains fail-closed:

- `require_atomic_authorization_fence` in [safety/runtime_no_go.py](./safety/runtime_no_go.py) unconditionally raises `RuntimeNoGo` for known unfenced operations.
- Guard call sites in [chat/service.py](./chat/service.py) and [interface/futuristic_app.py](./interface/futuristic_app.py) block authenticated/approved chat, protected memory mutation, profile/workspace writes, and session profile selection; the HTTP denial helper returns 503.
- `_validate_startup_configuration` in [interface/futuristic_app.py](./interface/futuristic_app.py) rejects production startup because production authentication, trusted account resolution, and complete route authorization are not implemented. [README.md](./README.md) labels development authentication unverified.
- Source tests assert selected denials, but were not run in this review. Repository write methods remain callable internally; this gate is not proof of a universal repository-level fence, a limitation the consolidated assessment correctly flags.

No reviewed report describes the account registry, current approval claim state, or proposed migration design as production approval. A persisted `EXECUTING` approval state is not evidence that production authentication or a protected effect is approved. Keep the NO-GO gate unchanged.

## Final readiness decision

**NOT READY for production authentication, real-data shared-authority migration, cutover, or an integrated prototype connected to current stores/runtime.** The report statuses and critical conclusions are substantively accurate; no **PROVEN** classification is improperly assigned to historical or environment-dependent readiness.

A synthetic-only prototype is **not currently authorized**. It may be separately considered only after the documented containment conditions are independently verified and explicit human authorization is recorded before execution. Such a harness would not close the historical key, ownership, SQLite durability, or recovery/cutover gates.

## Recommended next action

Keep all Phase 3G.1 NO-GO gates unchanged. Before any synthetic-only experiment, independently verify the documented isolation controls and obtain recorded human authorization for that specific run; otherwise defer it. Do not connect it to source data, secrets, production authentication, or protected runtime paths. For migration readiness, continue with a read-only provenance preflight over controlled immutable backups and key-custody records, producing reviewed per-record verified, quarantined, or unverifiable dispositions before any migration implementation is considered.

## Phase 3G.12 finding-closure addendum

The documentation corrections below address the three findings above. Closure is
limited to documentation: the prototype isolation controls have not been
implemented or independently tested, and the historical evidence gaps remain.

| Finding | Correction | Closure status |
|---|---|---|
| 1. Prototype scope/isolation was underspecified and “prototype” was used for distinct scopes. | [PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md](./PARMAR-SHARED-AUTHORITY-MIGRATION-DESIGN.md) now defines synthetic-only isolation requirements and requires independent verification plus recorded human authorization before each run. [PARMAR-RECOVERY-CUTOVER-READINESS-REPORT.md](./PARMAR-RECOVERY-CUTOVER-READINESS-REPORT.md) distinguishes those experiments from prototypes touching real data/current stores/production runtime. | **DOCUMENTATION CLOSED.** Synthetic isolation is still **NOT PROVEN** operationally; no prototype execution is authorized. |
| 2. “Readiness level 1” had no cited scale. | [PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md](./PARMAR-SHARED-AUTHORITY-READINESS-ASSESSMENT.md) defines evidence classifications and readiness levels 1–3, identifies this assessment as Level 1, and states levels are not approvals. | **CLOSED.** Current Level 1 and all evidence classifications are unchanged. |
| 3. HMAC report omitted the exact approval-key derivation label. | [PARMAR-HMAC-KEY-CONTINUITY-REPORT.md](./PARMAR-HMAC-KEY-CONTINUITY-REPORT.md) now records the source-verified label, byte encoding, and SHA-256 derivation. | **DOCUMENTATION CLOSED.** Historical key continuity remains **NOT PROVEN**. |

The detailed status and remaining uncertainty are recorded in
[PARMAR-SHARED-AUTHORITY-GAP-CLOSURE-REPORT.md](./PARMAR-SHARED-AUTHORITY-GAP-CLOSURE-REPORT.md).
