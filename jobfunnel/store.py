"""Persistence: jobs.json, seen.json, status.json.

Behaviour worth preserving from the original design:
- seen.json maps job id -> first_seen so the dashboard can show "new
  since" instead of "posted since".
- If every source fails, the previous jobs.json is left untouched
  rather than wiped - a bad network day must not look like "no jobs".
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Job, SourceReport


def load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
    return default


def load_seen(path: Path) -> dict[str, str]:
    data = load_json(path, {})
    return data if isinstance(data, dict) else {}


def write_json(path: Path, obj: Any) -> None:
    """Atomic write: temp file in the same directory, then os.replace.

    jobs.json is committed and served by GitHub Pages, so an interrupted
    write must never leave a truncated file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_jobs(path: Path, jobs: list[Job], generated_at: str) -> None:
    write_json(path, {"generated_at": generated_at, "count": len(jobs), "jobs": [j.to_dict() for j in jobs]})


def write_seen(path: Path, seen: dict[str, str]) -> None:
    write_json(path, seen)


def write_status(
    path: Path,
    sources: list[SourceReport],
    generated_at: str,
    filtered_out: int,
    merged_duplicates: int,
    note: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "generated_at": generated_at,
        "sources": [s.to_dict() for s in sources],
        "filtered_out": filtered_out,
        "merged_duplicates": merged_duplicates,
    }
    if note:
        payload["note"] = note
    write_json(path, payload)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
