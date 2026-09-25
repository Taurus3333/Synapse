"""Live / production-style validation against a running Synapse API.

  synapse-e2e
  # or:
  python scripts/run_e2e_validation.py
"""

from __future__ import annotations

from synapse.e2e.__main__ import main

if __name__ == "__main__":
    main()
