#!/usr/bin/env python3
"""Render a truthful per-emulator report for AETHER screenshot artifacts."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path.name} is not a valid PNG screenshot.")
    return struct.unpack(">II", header[16:24])


def render_report(artifact_directory: Path, profile: str, run_status: str) -> str:
    evidence_path = artifact_directory / "evidence.json"
    if not evidence_path.is_file():
        return (
            "# AETHER automated visual acceptance\n\n"
            "**Overall: FAIL.** The instrumentation test did not produce screenshot metadata.\n"
        )

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    screenshots = evidence.get("screenshots", [])
    expected_names = {
        "tabs-empty.png",
        "tabs-populated.png",
        "tabs-scrolled.png",
        "tabs-search-empty.png",
        "tabs-create-failure.png",
    }
    recorded_names = {item.get("file") for item in screenshots}
    missing_names = sorted(expected_names - recorded_names)
    if not screenshots:
        return (
            "# AETHER automated visual acceptance\n\n"
            "**Overall: FAIL.** "
            "No screenshot evidence was produced.\n"
        )

    test_status = (
        "PASS"
        if run_status == "PASS" and evidence.get("testStatus") == "PASS" and not missing_names
        else "FAIL"
    )
    rows = []
    for screenshot in screenshots:
        image_path = artifact_directory / screenshot["file"]
        if image_path.is_file():
            width_px, height_px = png_dimensions(image_path)
            dimensions_match = (
                width_px == screenshot.get("widthPx")
                and height_px == screenshot.get("heightPx")
            )
            status = test_status if dimensions_match else "FAIL"
            pixel_dimensions = f"{width_px}x{height_px}"
            findings = "None detected by the instrumentation assertions." if dimensions_match else (
                "PNG dimensions do not match the device evidence metadata."
            )
        else:
            status = "BLOCKED"
            pixel_dimensions = "missing"
            findings = "Screenshot file was not collected from the emulator."
        rows.append(
            f"| `{screenshot['file']}` | {profile} / {evidence.get('deviceModel', 'unknown')} "
            f"| {pixel_dimensions} px; {screenshot.get('widthDp', '?')}x"
            f"{screenshot.get('heightDp', '?')} dp at {screenshot.get('densityDpi', '?')} dpi "
            f"| {screenshot.get('state', 'unknown')} | {status} | {findings} |"
        )

    for name in missing_names:
        rows.append(
            f"| `{name}` | {profile} / {evidence.get('deviceModel', 'unknown')} "
            "| missing | expected screenshot state was not captured | FAIL "
            "| Required application-state screenshot was not produced. |"
        )

    overall = "FAIL" if test_status == "FAIL" else "PARTIAL"
    return "\n".join(
        [
            "# AETHER automated visual acceptance",
            "",
            f"**Overall: {overall}.** API 35 emulator profile `{profile}`; instrumentation result: **{test_status}**. "
            "Generated screenshots still require human visual review.",
            "",
            "| Screenshot | Emulator | Captured dimensions | Application state | Automated status | Findings |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
            "PASS records successful UI assertions and matching PNG metadata; it is not a substitute for visual or accessibility review.",
        ]
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_directory", type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--run-status", choices=("PASS", "FAIL"), required=True)
    args = parser.parse_args()
    args.artifact_directory.mkdir(parents=True, exist_ok=True)
    report = render_report(args.artifact_directory, args.profile, args.run_status)
    (args.artifact_directory / "visual-acceptance.md").write_text(report, encoding="utf-8")
    print(report, end="")
    if args.run_status == "PASS" and "**Overall: FAIL.**" in report:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
