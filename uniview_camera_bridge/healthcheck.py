from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

options_path = Path("/data/options.json")
status_path = Path("/config/status.json")
try:
    options = json.loads(options_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8"))
    # heartbeat continues while image processing is intentionally disabled;
    # checked remains the timestamp of the last real position check.
    timestamp = status.get("heartbeat") or status["checked"]
    checked = datetime.fromisoformat(timestamp)
    max_age = timedelta(seconds=max(180, int(options.get("check_interval_seconds", 120)) * 3))
    if datetime.now().astimezone() - checked > max_age:
        raise RuntimeError("status is stale")
except Exception as exc:
    print(exc)
    sys.exit(1)
sys.exit(0)
