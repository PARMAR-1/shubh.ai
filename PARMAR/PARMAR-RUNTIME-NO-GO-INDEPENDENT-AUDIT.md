# PARMAR Runtime NO-GO Independent Security Audit (Phase 3G.14)

## Revalidation update — 2026-11-25

The original audit below records the state assessed at that review. Reinspection
of the current source and focused offline tests now verifies mitigation of
Finding 1 for the reviewed entry points:

- The anonymous `/api/chat` route forces `selected_provider="local-demo"` and
  does not honor a client-requested external provider.
- External chat dispatch requires a trusted execution check in the
  orchestration path; direct calls without one are denied before provider
  invocation.
- Search dispatch requires an execution check and is denied when one is
  absent.
- Focused fake-provider/search tests passed: the Local Demo HTTP flow, the
  anonymous chat/research flow, direct external-chat denial, and direct
  search denial (4 passed, 2026-11-25).

**Current Finding 1 status: MITIGATED IN CURRENT SOURCE; focused tests pass.**
Independent Security review and deployment/configuration assessment remain
pending. No live provider or deployment was accessed. The protected-operation
atomic-fence remains **NO-GO**; this revalidation does not clear it or enable
production authentication.

## Executive verdict

**Historical verdict: FAIL at the time of the original audit.**

The executive summary, entry-point inventory and detailed findings below
preserve the original audit snapshot; use the revalidation above for the
current Finding 1 status.

At the time of that audit, the inspected Phase 3G.1 guard was unconditional,
and authenticated protected chat, approval continuation, memory mutation,
profile/workspace writes, and session/profile selection appeared to reach
fail-closed checks before their protected effects. However, an anonymous
`POST /api/chat` path could reach ordinary chat and research/provider dispatch
without the authenticated NO-GO check or a required execution-authorization
callback. If an external chat or search provider was enabled in server
configuration, this path could cause unauthenticated outbound provider
requests. That violated the documented anonymous Local Demo boundary and
meant the reviewed entry points did not reliably contain all consequential
paths.

The original review was static. It did not establish whether an affected
deployment had external providers enabled or whether the service was reachable
from an untrusted network. Tests had not been run at that time.

## Protected-operation inventory

| Operation / entry point | Guard and side-effect order | Assessment |
|---|---|---|
| Authenticated `POST /api/chat` | `interface/futuristic_app.py` routes through `_deny_unfenced_protected_operation` for `AUTHENTICATED_CHAT` before the authenticated chat path proceeds. `ChatService.respond` also contains NO-GO checks, but the ordinary chat guard is conditional on its memory callback. | **DENIED on the authenticated route.** The direct service guard is not universal; see Finding 1. |
| Approved chat continuation | The HTTP approval route checks `APPROVED_CHAT_CONTINUATION` before calling the approval execution claim. `ChatService.respond_approved` and its internal approved-review entry check NO-GO before provider continuation. | **DENIED on the reviewed route and direct approved-service path.** No approval claim/provider call should follow these guards. |
| Protected memory mutation | The memory mutation HTTP route calls `_deny_unfenced_protected_operation` for `PROTECTED_MEMORY_MUTATION` before repository mutation. | **DENIED on the reviewed route.** |
| Profile/workspace writes | Profile/workspace HTTP routes call the NO-GO helper with `PROFILE_WORKSPACE_WRITE` before repository writes. | **DENIED on the reviewed routes.** |
| Session profile selection | The session/profile route checks `SESSION_PROFILE_SELECTION` before changing session profile context. | **DENIED on the reviewed route.** |
| Anonymous `POST /api/chat` (original audit snapshot) | The anonymous branch did not run the authenticated-chat NO-GO check and called `ChatService.respond`. `research: true` could reach `SearchService.search`. | **BYPASS / FAIL at original audit.** Current revalidation is recorded above. |
| Public stateless analysis and anonymous Local Demo (original audit snapshot) | Documented as public/local demo behavior, but the anonymous chat service route could use configured external providers or search. | **Boundary not reliably enforced at original audit** for outbound provider/research activity. |

The principal guard is `require_atomic_authorization_fence` in
[safety/runtime_no_go.py](./safety/runtime_no_go.py). Its known-operation
behavior is fail-closed: it always raises `RuntimeNoGo`. The concern is not a
flag that disables this function; it is an alternate route/service path that
does not call it.

## Findings

### Finding 1 — Anonymous chat can bypass the intended Local Demo boundary

**Severity: MEDIUM**  
**Confidence: 8/10**  
**Status at original audit: OPEN.**

**Current revalidation status:** Mitigated in current source; focused tests
passed on 2026-11-25. Independent Security/deployment closure remains pending.

**Evidence and source locations**

- [interface/futuristic_app.py](./interface/futuristic_app.py):
  anonymous chat branch around lines 2237–2261 skips the authenticated-chat
  guard and calls `ChatService.respond`; research handling is around
  lines 2427–2431.
- [chat/service.py](./chat/service.py): `ChatService.respond` checks
  `AUTHENTICATED_CHAT` only when the optional memory callback is supplied;
  ordinary calls without that callback can continue.
- [research/service.py](./research/service.py): `SearchService.search`
  checks the execution callback only if one is supplied; an unguarded direct
  call has no default NO-GO denial.
- [chat/readiness.py](./chat/readiness.py): provider readiness/configuration
  supports externally configured providers. This is server-side configuration,
  not proof that any provider is enabled in deployment.

This is an authorization/boundary concern even though the route is anonymous:
anonymous Local Demo behavior is documented as not sending chat to an external
provider. The optional callbacks are not a sufficient fail-closed boundary
when omitted by a route or direct caller.

**Regression-test evidence inspected (not executed)**

- `test_authenticated_research_is_runtime_no_go_before_search_or_history_write`
  covers the authenticated research denial and checks the search/history side
  effects.
- `test_anonymous_chat_cannot_select_authenticated_conversation_and_creates_no_record`
  checks conversation isolation but does not assert that chat/search providers
  were never called.
- `test_direct_service_calls_cannot_bypass_unfenced_protected_operations`
  covers approved continuation and memory retrieval, not ordinary anonymous
  provider or search dispatch.

The test gap is not merely an HTTP status assertion: the anonymous test does
not verify zero provider/search calls.

**Recommended follow-up, ordered by severity**

1. Keep anonymous/local-demo requests from dispatching external chat or
   research providers; fail closed at the HTTP route and service boundary
   unless a separately approved trusted context explicitly permits the effect.
2. Add focused tests with fake remote chat and search providers, enabled only
   by test configuration. Exercise anonymous `/api/chat` with `research` both
   false and true, and direct `ChatService.respond` / `SearchService.search`
   calls without execution callbacks. Assert zero external-provider/search
   calls and no protected persistence side effects.
3. Retain tests for authenticated chat, approved continuation, memory
   mutation, profile/workspace writes, and profile selection that assert the
   guard fires before the relevant side-effect spy or approval claim.

No fix was made in this read-only audit.

### Finding 2 — Documentation and test names can imply approved execution

**Severity: LOW**  
**Confidence: 8/10**  
**Status at audit time: OPEN; current revalidation: CLOSED**

- [VOKKI-PART13-PRIVACY-SECURITY-GOVERNANCE-REPORT.md](./VOKKI-PART13-PRIVACY-SECURITY-GOVERNANCE-REPORT.md)
  and [PARALLEL-DEVELOPMENT-MASTER-PLAN.md](./PARALLEL-DEVELOPMENT-MASTER-PLAN.md)
  contained descriptions at audit time that approved continuation resumes
  provider generation.
- `tests/test_approval_integrity.py` included at audit time
  `test_approved_chat_uses_original_review_calls_provider_once_and_releases_pass`;
  the inspected assertions expected NO-GO and zero provider calls, so the name
  could suggest behavior the test no longer permitted.
- The test named
  `test_pending_approval_survives_store_restart_and_continues` at audit time
  verified persistence/approval state and `execution_allowed=True` for a
  review-only approval record; it did not demonstrate provider execution.

**Recommendation at audit time:** Update the stale documentation and rename or
clarify the tests so they distinguish persistence/state-machine behavior from
runtime-authorized provider execution. Preserve assertions that the current
NO-GO blocks actual provider execution.

**Current revalidation:** The misleading continuation claims were corrected in
the Part 13 report, master plan, README, and Master PRD. The two originally
cited test names now describe the NO-GO denial and single-use approval-state
redemption rather than implying provider execution. Three additional
route-test names that described unreachable provider/validator paths were also
clarified. The original findings above record the audit-time state; this
documentation/test-name issue is now closed. The focused approval integrity
and persistence suites passed: 74 tests. `git diff --check` passed.

## Entry-point and bypass-path analysis

- The authenticated routes for protected chat, approved continuation,
  memory mutation, profile/workspace writes, and session-profile selection
  appear to invoke the guard before the relevant protected call.
- Direct approved-chat service methods are guarded, preventing a caller from
  bypassing the HTTP denial by invoking the approved continuation service.
- Direct ordinary chat and search services are not unconditionally guarded
  when their optional execution callback is absent. This leaves an alternate
  internal path as well as the anonymous HTTP branch.
- The guard has no environment, request-body, frontend, development-auth, or
  client-controlled override in `require_atomic_authorization_fence`.
  The anonymous route is a control-flow omission, not an override.
- No other specific alternate HTTP bypass was established by this review
  beyond the anonymous chat branch described above. Repository write methods
  remain internally callable; route-level guards are not a universal
  repository-level fence.

## Verified behavior versus unverified behavior at original audit time

**Verified by source inspection:** the guard raises for known unfenced
operations; reviewed authenticated/protected HTTP paths call it; production
startup in `_validate_startup_configuration` in
[interface/futuristic_app.py](./interface/futuristic_app.py) rejects production
because trusted authentication/account resolution and complete route
authorization are not implemented; direct approved-chat service paths are
guarded.

**At original audit time, not verified:** tests had not been run; no live
deployment or provider configuration had been inspected; actual network
reachability, provider activation, and exposure of `/api/chat` had not been
tested. The 2026-11-25 tests are offline and do not establish deployment
configuration or network exposure.

## Original audit final assessment (historical)

At the original audit, the anonymous path could reach provider/research
dispatch. That finding is superseded for the reviewed current source paths by
the revalidation above. Finding 2 remains a low-severity documentation/test-
naming issue. Production exposure and all deployment assumptions remain
unverified. Keep all existing NO-GO gates unchanged and do not enable
production authentication, providers, or protected execution.
