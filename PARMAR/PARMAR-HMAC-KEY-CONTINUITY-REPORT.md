# PARMAR Historical HMAC Key Continuity Report

**Scope and handling:** Static inspection of current source, schemas, docs, and test names only. No live database, environment secret, secret-manager history, or secret value was read. No test or migration was run. No tag value, secret-derived hash, or credential is reproduced here.

## Executive finding

**Historical key continuity: NOT PROVEN.** Source code establishes current key inputs and algorithms. It does not establish historical deployed key values, rotation history, or a record-to-key mapping. Schema version and HMAC format version are not key identifiers. No current source evidence can establish which key verifies any extant production row.

## HMAC-protected data and key paths

### Sessions

- **Source and purpose:** [sessions.py](./sessions.py), `SQLiteSessionRepository.from_environment` reads `PARMAR_SESSION_STORE_KEY`, encodes it as UTF-8 bytes, and requires at least 32 bytes. `SQLiteSessionRepository.__init__` accepts that key as `integrity_key`; it is not derived or stored in SQLite.
- **Session record format:** `_session_integrity_tag` serializes an ordered JSON array with compact separators and ASCII encoding, then computes HMAC-SHA-256 and lowercase hexadecimal output. The domain prefix is selected from `PARMAR-SESSION-RECORD-v1:` through `v4:` according to row fields:
  - v1: session IDs, token hash, user ID, authentication source, lifecycle timestamps/status, revocation and replacement fields.
  - v2: v1 data plus active profile ID.
  - v3: account identity and authentication interval, plus active profile ID when present.
  - v4: v3 fields plus identity-evidence ID.
- `_session_from_row` reconstructs and verifies a row using the current configured key and the format selected by its fields. There is no stored tag-format or key ID.
- **Schema eras:** `SQLiteSessionRepository._initialize` accepts `user_version` 1, 2, 3, or 4. v1→v2 adds profile scope; v2→v3 adds account identity/authentication timestamps; v3→v4 adds the evidence ID/table and revokes active trusted sessions lacking evidence lifecycle rows. Schema v4 can therefore contain records authenticated with older v1–v3 tag formats.

### Identity evidence

- **Source and purpose:** The same raw `PARMAR_SESSION_STORE_KEY` bytes are passed to `_identity_evidence_integrity_tag`.
- **Format:** HMAC-SHA-256 over compact ASCII JSON for evidence ID, serialized account identity, authentication/expiry times, state, and session ID, prefixed with `PARMAR-IDENTITY-EVIDENCE-v1:`. Output is lowercase hexadecimal. `_identity_evidence_from_row` validates the format and compares the tag using `hmac.compare_digest`.
- **Schema era:** The evidence table is introduced by session schema v4. Its tag format is v1; there is no independent evidence key ID, key ring, or per-row version.

### Approvals and lifecycle/cancellation data

- **Source and purpose:** [interface/approvals.py](./interface/approvals.py), `SQLitePendingApprovalStore.from_environment` chooses `PARMAR_APPROVAL_STORE_KEY` when nonempty, otherwise `PARMAR_SESSION_STORE_KEY`; the chosen string must encode to at least 32 UTF-8 bytes. The approval-specific key is `SHA-256(b"PARMAR-APPROVAL-STORE-v1\0" + secret.encode("utf-8"))`. The prefix is a non-secret domain-separation label; the derived key is not stored.
- **Record format:** `_tag` applies HMAC-SHA-256 to UTF-8 encoding of the NUL-delimited token hash, current state, expiry string, and exact stored payload string. The hexdigest is lowercase hexadecimal. `_read_pending` and migrations verify with the configured, currently derived key. The tag has no per-row key or format identifier.
- **Schema eras:** `SQLitePendingApprovalStore._initialize` accepts `user_version` 1, 2, or 3. v1→v2 validates and copies the original tag for legacy states; v2→v3 validates and copies tags while expanding lifecycle states to include `EXECUTING`. Both migrations use the current `_tag` algorithm/key. Approval schema v3 allows `PENDING`, `APPROVED`, `EXECUTING`, `REJECTED`, `CANCELLED`, `EXPIRED`, and `CONSUMED`; it has no `RECOVERY_REQUIRED`.
- **Cancellation:** There is no separate append-only cancellation table or independent cancellation signature. `_cancellation_audit` adds a lifecycle object inside the approval payload; it includes actor, session, decision, binding fingerprint, reason, and time. `cancel` changes the state and payload and recomputes the record HMAC. Thus the current cancellation evidence is covered by the current approval tag, but historical transition envelopes are not retained as a sequence.
- `EXECUTING` is a persisted state in a separate approval database. Current transitions permit `EXECUTING`→`CONSUMED`, `CANCELLED`, or `EXPIRED`; the source does not encode the migration design’s indeterminate terminal disposition.

### Other HMAC use found

- [sessions.py](./sessions.py), `SessionManager.csrf_token`: a separate HMAC-SHA-256 token is computed over `PARMAR-CSRF-v1:` plus the session UUID bytes and base64url-encoded without padding. `SessionManager.__init__` uses an injected `csrf_secret` or generates 32 random bytes with `secrets.token_bytes`. This is a process/session-bound CSRF token, not a stored signed record and not an authority-migration record.
- A repository-wide source search found no other persistent HMAC record implementation. Other SHA-256 uses include unkeyed token/request fingerprints; they are not HMAC signatures. The JSONL decision log is not signed. The backup runbook says its checksum sidecar is unsigned and is not provenance evidence.

## Historical recovery and association

**No authoritative historical key evidence was found.** Current environment settings and current source algorithms do not prove what historical processes used. No repository schema stores a key ID, and no source-controlled key ring or rotation manifest maps database/schema eras to keys. A stable-key configuration instruction is not evidence that the key was never rotated. The configured approval key can also differ from the session key; a fallback does not prove which path was used for a historical database.

No live rows were inspected, so the number and distribution of session/evidence/approval rows by schema, tag format, or historical key are unknown. Do not assume the current environment key validates them. Treat each record as **unverifiable** until the original key source and exact format are established and its HMAC is validated. Any record that fails, has an ambiguous key candidate, or cannot be associated with an authoritative source era must be quarantined and remain non-authorizing; do not re-sign it as if verified.

## Test evidence (source inspected; tests not run)

Relevant test names include:

- [tests/test_sessions.py](./tests/test_sessions.py): v1 and v2 session migrations, v3 trusted session without evidence migration, tampered identity evidence, session restart/tamper/corrupt-storage handling, and CSRF binding.
- [tests/test_approval_persistence.py](./tests/test_approval_persistence.py): v1/v2 approval schema migrations, cancellation/restart, cancellation/consume serialization, execution-claim exclusion, non-ASCII tag rejection, tampered-record handling, and session-key fallback.
- [tests/test_approval_integrity.py](./tests/test_approval_integrity.py): approval binding, owner/session checks, cancellation, and replay cases.

These are source-level test inventory only; no tests were executed. The named tests demonstrate coverage of selected current schema migrations, tampering, lifecycle, and concurrency cases. No inspected test establishes production historical key custody, key rotation/version recovery, independent golden tag fixtures from every deployed key era, or a database-row-to-key manifest. Migration tests that use the current implementation/key cannot prove deployed historical continuity.

## Evidence required before migration

1. Establish controlled historical key custody and rotation evidence without putting values in logs or reports. It must distinguish approval, session/evidence, CSRF, and destination-key purposes and identify whether the approval source was its dedicated key or session-key fallback.
2. Obtain quiesced, immutable backups and a source inventory containing database identity, schema version, and row primary keys. Do not use a live database for discovery.
3. Build a restricted mapping from each source database and row-format era to its original key and serializer/tag format. Validate every row and independently reviewed golden fixtures; never infer a key from a valid-looking tag or a matching identity value.
4. Record per-row `VERIFIED`, `QUARANTINED`, or `UNVERIFIABLE` disposition outside the ordinary report/log path. Only verified source records may be transformed and re-signed under a separately versioned destination envelope.
5. Keep all unverified sessions, evidence, approvals, and cancellation data inaccessible to authorization. Do not activate if a required record’s source integrity cannot be demonstrated.

**Conclusion:** Key algorithms and current configuration paths are **partially evidenced**; historical key recovery and association are **NOT PROVEN**. No key-continuity prerequisite is satisfied for migration.
