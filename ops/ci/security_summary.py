"""Print finding locations, never matched secrets or source-code excerpts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def summary(kind: str, payload: Any) -> dict[str, Any]:
    if kind == "bandit":
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("invalid scanner report")
        if payload.get("errors") or payload.get("metrics", {}).get("_totals", {}).get("loc", 0) < 1:
            raise ValueError("scanner did not complete all source files")
        items = payload["results"]
        fields = ("filename", "line_number", "test_id", "issue_severity", "issue_confidence")
    elif kind == "gitleaks":
        if not isinstance(payload, list):
            raise ValueError("invalid scanner report")
        items = payload
        fields = ("File", "StartLine", "RuleID")
    else:
        raise ValueError("unknown scanner")
    locations = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid scanner finding")
        location = {field: item.get(field) for field in fields}
        if any(not isinstance(value, (str, int)) for value in location.values()):
            raise ValueError("invalid scanner location")
        locations.append(location)
    return {"scanner": kind, "count": len(items), "locations": locations}


def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError("invalid arguments")
        payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        # ASCII JSON prevents source-controlled newlines from becoming workflow commands.
        print(json.dumps(summary(sys.argv[1], payload), ensure_ascii=True))
    except (OSError, ValueError, TypeError, AttributeError):
        print("Security report is missing, incomplete or invalid.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
