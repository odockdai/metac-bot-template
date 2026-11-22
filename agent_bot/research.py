"""Hand-off between the forecasting step and the research agent, through files."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from agent_bot import ledger

PROTOCOL_FILE = Path(__file__).with_name("RESEARCH_PROTOCOL.md")
RESEARCH_BUDGET = timedelta(minutes=45)
CLOSE_MARGIN = timedelta(minutes=75)

ResearchStatus = Literal["missing", "ready", "partial"]


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


def build_request(question_id: int, details: str, output_path: Path, deadline: datetime) -> str:
    return "\n".join(
        [
            f"Research request for the Metaculus forecasting bot (question {question_id}). "
            "Do not reply; write the brief to the file.",
            "",
            f"Output path: {output_path}",
            f"Deadline (UTC): {deadline.strftime('%Y-%m-%d %H:%M')}",
            "",
            PROTOCOL_FILE.read_text().strip(),
            "",
            "## Question",
            "",
            details.strip(),
        ]
    )
