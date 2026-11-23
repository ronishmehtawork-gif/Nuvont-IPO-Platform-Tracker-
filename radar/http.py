from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

USER_AGENT = os.getenv(
    "RADAR_USER_AGENT",
    "Mozilla/5.0 (compatible; sme-drhp-radar/0.1; personal research tool)",
)


@dataclass
class FetchResult:
    ok: bool
    status: int | None
    text: str
    error: str = ""


class Fetcher:
    """Polite fetcher. Never tries to get around a block: a 403 or 429 is reported, not retried."""

    def __init__(self, snapshot_dir: str | None = None, timeout: int = 20):
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}
        )
        self.timeout = timeout
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        if self.snapshot_dir:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def get(self, name: str, url: str) -> FetchResult:
        result = FetchResult(False, None, "", "no attempt")
        for attempt in range(2):
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                result = FetchResult(False, None, "", type(e).__name__)
                time.sleep(2)
                continue
            result = FetchResult(
                200 <= r.status_code < 300 and bool(r.text),
                r.status_code,
                r.text,
                "" if r.ok else f"HTTP {r.status_code}",
            )
            if r.status_code < 500:
                break
            time.sleep(2)
        self._snapshot(name, result)
        return result

    def _snapshot(self, name: str, result: FetchResult) -> None:
        if not self.snapshot_dir:
            return
        body = result.text[:300_000] if result.text else f"<!-- {result.error} -->"
        (self.snapshot_dir / f"{name}.html").write_text(body, encoding="utf-8")
