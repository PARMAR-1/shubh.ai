# PARMAR UI visual acceptance

## Current cycle status

**Overall: BLOCKED.** The pinned visual-test workflows and report generators are implemented, but this worktree has no connected Android device/emulator, and its cached Chromium cannot launch because system libraries including `libglib-2.0.so.0` are unavailable. No screenshots were produced in this environment, and the GitHub Actions workflows were not run here. Do not treat compilation, test execution, or future screenshot generation as visual approval.

## Reproducible CI configuration

- VOKKI: Node `22.23.5`, `@playwright/test` `1.65.1`, bundled Chromium revision `1255` (`159.0.8154.6`); CI installs that matching browser and Linux dependencies with Playwright.
- AETHER: Android API `35`, `google_apis`, `x86_64`, `pixel_2` emulator profile; display overrides use density `320 dpi` and logical viewports `320x640`, `390x844`, and `800x1200 dp`.

## PARMAR destination navigation

No destination-navigation control may be fixed, sticky, floating, or persistent at the bottom of a PARMAR screen. This includes bottom docks, destination tab strips, and VOKKI ecosystem launchers. Desktop sidebar/top navigation is allowed; compact layouts must retain keyboard- and screen-reader-accessible navigation through the top menu and side drawer.

`PARMAR/interface/visual-tests/navigation-regression.spec.cjs` exercises every sidebar destination through the rendered application at `1440x1000`, `768x1024`, and `390x844`. It checks for bottom-anchored navigation, horizontal overflow, accessible labels, drawer Escape handling, and focus restoration. To run it from `PARMAR/interface/visual-tests`, use `npm test -- navigation-regression.spec.cjs`. A test run and reviewed screenshots are still required before visual acceptance can be marked PASS.

| Product and target | Status | Evidence required |
|---|---|---|
| VOKKI, 1440x1000 desktop | BLOCKED | Pinned Playwright screenshot, automated checks, and human review |
| VOKKI, 768x1024 tablet | BLOCKED | Pinned Playwright screenshot, automated checks, and human review |
| VOKKI, 390x844 mobile | BLOCKED | Pinned Playwright screenshot, automated checks, and human review |
| AETHER, 320x640 dp narrow phone | BLOCKED | API 35 emulator screenshot, UI assertions, and human review |
| AETHER, 390x844 dp phone | BLOCKED | API 35 emulator screenshot, UI assertions, and human review |
| AETHER, 800x1200 dp tablet-sized viewport | BLOCKED | API 35 emulator screenshot, UI assertions, and human review |

## Report semantics

The VOKKI workflow writes `visual-acceptance.md` alongside Playwright screenshots and reports. The Android emulator job records the device profile and screenshot pixel/dp dimensions, then writes a report beside its screenshot files.

- **PASS** means the named automated assertion passed.
- **FAIL** means an automated assertion or test failed.
- **PARTIAL** means automation and screenshots passed, but a person has not reviewed the rendered images.
- **BLOCKED** means the workflow/device run was unavailable, so no attempt produced evidence. Once a CI run begins, missing expected screenshots or metadata are a **FAIL**.

Every screenshot report records the viewport/device size, app state, detected overflow/clipping/overlap, and findings. Screenshot production alone never marks a product visually accepted.
