"""
Logs the start of a cleaning cycle to run_log.json.
Runs immediately after dolphin_start.py in the same GitHub Actions job.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path(__file__).parent / "run_log.json"


def main():
    log = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []

    entry = {
        "id": len(log) + 1,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "stop_time": None,
        "duration_minutes": None,
        "status": "running",
    }

    log.append(entry)
    LOG_FILE.write_text(json.dumps(log, indent=2))
    print(f"Logged start: {entry['start_time']}")


if __name__ == "__main__":
    main()
