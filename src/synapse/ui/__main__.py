"""Launch Streamlit demo UI."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    app = Path(__file__).resolve().parent / "app.py"
    sys.argv = [
        "streamlit",
        "run",
        str(app),
        "--server.headless",
        "true",
        "--server.address",
        "0.0.0.0",
        "--browser.gatherUsageStats",
        "false",
    ]
    from streamlit.web import cli as stcli

    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
