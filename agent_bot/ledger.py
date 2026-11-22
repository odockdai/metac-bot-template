"""Append-only local record of every submission, kept outside the git repo.

Forecast values stay here and in the private Metaculus comment only, so the
public repo never shows a forecast on an open question.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def state_dir() -> Path:
    path = Path(os.getenv("AGENT_BOT_STATE_DIR", ".agent_state")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path() -> Path:
    return state_dir() / "ledger.jsonl"


@contextmanager
def exclusive_lock() -> Iterator[None]:
    with open(state_dir() / "submit.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_entries() -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append(entry: dict) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(), **entry}
    with open(ledger_path(), "a") as handle:
        handle.write(json.dumps(entry) + "\n")


def latest_by_question() -> dict[int, dict]:
    """Newest forecast/comment event per question; research requests are tracked apart."""
    latest: dict[int, dict] = {}
    for entry in read_entries():
        if entry["event"] != "research_requested":
            latest[int(entry["question_id"])] = entry
    return latest


def forecasted_question_ids() -> set[int]:
    return {
        qid
        for qid, entry in latest_by_question().items()
        if entry["event"] in {"forecast_posted", "comment_posted", "comment_failed"}
    }


def research_requested_at(question_id: int) -> datetime | None:
    times = [
        datetime.fromisoformat(e["at"])
        for e in read_entries()
        if e["event"] == "research_requested" and int(e["question_id"]) == question_id
    ]
    return max(times) if times else None


def comments_pending() -> list[dict]:
    return [e for e in latest_by_question().values() if e["event"] in {"forecast_posted", "comment_failed"}]
