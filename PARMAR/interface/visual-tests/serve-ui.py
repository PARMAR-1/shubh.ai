"""Start the real PARMAR UI on a test-selected loopback port."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PARMAR.interface import futuristic_app


def main() -> None:
    port = int(os.environ.get("PARMAR_VISUAL_TEST_PORT", "8080"))
    if not 1024 <= port <= 65535:
        raise ValueError("The visual-test port must be between 1024 and 65535.")
    futuristic_app.UI_PORT = port
    futuristic_app.main()


if __name__ == "__main__":
    main()
