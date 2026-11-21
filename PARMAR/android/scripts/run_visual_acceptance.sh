#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: run_visual_acceptance.sh PROFILE SCREEN_SIZE DENSITY" >&2
  exit 64
fi

profile=$1
screen_size=$2
density=$3

case "$profile" in
  narrow-phone-320dp|phone-390dp|tablet-800dp) ;;
  *)
    echo "Unsupported AETHER visual profile: $profile" >&2
    exit 64
    ;;
esac
[[ "$screen_size" =~ ^[0-9]+x[0-9]+$ ]] || {
  echo "Screen size must use WIDTHxHEIGHT." >&2
  exit 64
}
[[ "$density" =~ ^[1-9][0-9]*$ ]] || {
  echo "Density must be a positive integer." >&2
  exit 64
}

python3 -m unittest scripts.test_render_visual_acceptance

evidence_dir="artifacts/aether/${profile}"
mkdir -p "$evidence_dir"
trap 'status=$?; adb logcat -d > "$evidence_dir/emulator-logcat.txt" 2>&1 || true; exit "$status"' EXIT

adb shell wm size "$screen_size"
adb shell wm density "$density"
adb shell wm size | grep -Fq "$screen_size"
adb shell wm density | grep -Fq "$density"
{
  echo "Requested logical profile: $profile"
  echo "Emulator profile: pixel_2 / API 35 / google_apis / x86_64"
  adb shell wm size
  adb shell wm density
} > "$evidence_dir/device.txt" 2>&1

adb shell pm clear org.parmar.aether >/dev/null 2>&1 || true
adb logcat -c
test_status=PASS
if ! ./gradlew --no-daemon --stacktrace :app:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=org.parmar.aether.ui.tabs.AetherTabsVisualAcceptanceTest; then
  test_status=FAIL
fi

if adb shell run-as org.parmar.aether test -f cache/aether-visual/evidence.json >/dev/null 2>&1; then
  adb exec-out run-as org.parmar.aether cat cache/aether-visual/evidence.json > "$evidence_dir/evidence.json"
fi
for screenshot in tabs-empty tabs-populated tabs-scrolled tabs-search-empty tabs-create-failure; do
  if adb shell run-as org.parmar.aether test -f "cache/aether-visual/${screenshot}.png" >/dev/null 2>&1; then
    adb exec-out run-as org.parmar.aether cat "cache/aether-visual/${screenshot}.png" > "$evidence_dir/${screenshot}.png"
  fi
done
if ! python3 scripts/render_visual_acceptance.py "$evidence_dir" --profile "$profile" --run-status "$test_status"; then
  test_status=FAIL
fi
adb logcat -d > "$evidence_dir/emulator-logcat.txt" 2>&1 || true

if [[ "$test_status" != PASS ]]; then
  exit 1
fi
