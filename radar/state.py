from __future__ import annotations

import json
from pathlib import Path


def empty_state() -> dict:
    return {"version": 1, "started": False, "seen": {}, "health": {}, "last_heartbeat": ""}


def load(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return empty_state()
    state = json.loads(p.read_text(encoding="utf-8"))
    for k, v in empty_state().items():
        state.setdefault(k, v)
    return state


def save(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(state, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
