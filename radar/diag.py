"""A small public health file (data/diagnostics.json) that says what each data job did on its last run.

It holds counts and short error text only, never secrets, ids or full page bodies, because the repo is public.
The file is rewritten only when a section's content changes, so quiet runs cause no commit."""
from __future__ import annotations

import json
import os
from pathlib import Path


def path() -> str:
    return os.getenv("RADAR_DIAG", "data/diagnostics.json")


def record(section: str, info: dict, file: str | None = None) -> None:
    """Never raises: a diagnostics problem must not stop a data job."""
    target = Path(file or path())
    try:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get(section) == info:
            return
        data[section] = info
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
