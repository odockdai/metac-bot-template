"""Hand-off between the forecasting step and the research agent, through files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from agent_bot import ledger

PROTOCOL_FILE = Path(__file__).with_name("RESEARCH_PROTOCOL.md")
RESEARCH_BUDGET = timedelta(minutes=45)
CLOSE_MARGIN = timedelta(minutes=75)

ResearchStatus = Literal["missing", "ready", "partial"]


@dataclass(frozen=True)
class ResearchItem:
    question_id: int
    details: str
    output_path: Path
    closes_at: datetime | None
    deadline: datetime


def research_dir() -> Path:
    default = ledger.state_dir() / "research"
    path = Path(os.getenv("AGENT_BOT_RESEARCH_DIR", str(default))).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_path(question_id: int) -> Path:
    return research_dir() / f"{question_id}.md"


def research_status(path: Path) -> ResearchStatus:
    if not path.exists():
        return "missing"
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if lines and lines[-1].startswith("STATUS: complete"):
        return "ready"
    if lines and lines[-1].startswith("STATUS: partial"):
        return "partial"
    return "missing"


def research_deadline(now: datetime, close_time: datetime | None) -> datetime:
    deadline = now + RESEARCH_BUDGET
    if close_time is not None:
        deadline = min(deadline, close_time - CLOSE_MARGIN)
    return deadline


def _utc(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "unknown"


def build_request(items: list[ResearchItem]) -> str:
    ordered = sorted(items, key=lambda item: (item.deadline, item.closes_at or datetime.max.replace(tzinfo=item.deadline.tzinfo)))
    lines = [
        f"Research request for the Metaculus forecasting bot ({len(ordered)} question(s)). "
        "Do not reply; write one brief per question to its output path. "
        "Work in the order listed (earliest deadline first). If you cannot finish one in time, "
        "write what you have with `STATUS: partial` before its deadline and move on.",
        "",
        PROTOCOL_FILE.read_text().strip(),
    ]
    for number, item in enumerate(ordered, start=1):
        lines += [
            "",
            f"## Question {number} of {len(ordered)} (id {item.question_id})",
            "",
            f"Output path: {item.output_path}",
            f"Deadline for the brief (UTC): {_utc(item.deadline)}",
            f"Question closes (UTC): {_utc(item.closes_at)}",
            "",
            item.details.strip(),
        ]
    return "\n".join(lines)
