"""Problem-only monitor: reads Metaculus and the local ledger, never forecast values.

    uv run python -m agent_bot.monitor --tournament minibench --tournament fall-futureeval-2026

Prints a JSON report. `problems` is empty when nothing needs attention. It reports
presence only (forecast yes/no, private comment yes/no), so an operator can run it
on open scored questions without seeing what the bot forecast.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import dotenv
import requests

dotenv.load_dotenv()

from bot_helpers import silence_noisy_dependencies  # noqa: E402

silence_noisy_dependencies()

from forecasting_tools import MetaculusClient  # noqa: E402
from forecasting_tools.helpers.metaculus_client import ApiFilter  # noqa: E402

from agent_bot import ledger  # noqa: E402

URGENT_MINUTES = 60
LOOKBACK_DAYS = 3
STALE_RUN_MINUTES = 60


def private_comment_post_ids(client: MetaculusClient, user_id: int) -> set[int]:
    """Private bot comments are only listed with is_private=true."""
    post_ids: set[int] = set()
    url = f"{client.base_url}/comments/"
    params: dict[str, object] = {"author": user_id, "is_private": "true", "limit": 100, "offset": 0}
    while True:
        response = requests.get(url, params=params, **client._get_auth_headers(), timeout=30)
        response.raise_for_status()
        results = response.json().get("results", [])
        post_ids.update(int(c["on_post"]) for c in results if c.get("on_post"))
        if len(results) < 100:
            return post_ids
        params["offset"] = int(params["offset"]) + 100


def last_run_age_minutes(now: datetime) -> int | None:
    marker = ledger.state_dir() / "last_run.json"
    if not marker.exists():
        return None
    finished = datetime.fromisoformat(json.loads(marker.read_text())["finished_at"])
    return int((now - finished).total_seconds() // 60)


def check(tournaments: list[str]) -> dict:
    client = MetaculusClient()
    now = datetime.now(timezone.utc)
    user_id = client.get_current_user_id()
    commented = private_comment_post_ids(client, user_id)
    problems: list[dict] = []
    summary: dict[str, dict[str, int]] = {}

    for tournament in tournaments:
        target: str | int = int(tournament) if tournament.isdigit() else tournament
        open_qs = client.get_all_open_questions_from_tournament(target)
        closed_filter = ApiFilter(
            allowed_tournaments=[target],
            allowed_statuses=["closed", "resolved"],
            close_time_gt=now - timedelta(days=LOOKBACK_DAYS),
            group_question_mode="unpack_subquestions",
        )
        recent_closed = asyncio.run(client.get_questions_matching_filter(closed_filter))
        counts = {"open": len(open_qs), "open_forecasted": 0, "closed_recent": len(recent_closed), "closed_missed": 0}

        for q in open_qs:
            minutes = int((q.close_time - now).total_seconds() // 60) if q.close_time else None
            if q.already_forecasted:
                counts["open_forecasted"] += 1
                if q.id_of_post not in commented:
                    problems.append({"kind": "forecast_without_private_comment", "tournament": tournament, "question_id": q.id_of_question, "post_id": q.id_of_post})
            elif minutes is not None and minutes <= URGENT_MINUTES:
                problems.append({"kind": "open_unforecasted_closing_soon", "tournament": tournament, "question_id": q.id_of_question, "minutes_left": minutes})

        for q in recent_closed:
            if not q.already_forecasted:
                counts["closed_missed"] += 1
                problems.append({"kind": "missed_question", "tournament": tournament, "question_id": q.id_of_question, "closed_at": q.close_time.isoformat() if q.close_time else None})
            elif q.id_of_post not in commented:
                problems.append({"kind": "forecast_without_private_comment", "tournament": tournament, "question_id": q.id_of_question, "post_id": q.id_of_post})
        summary[tournament] = counts

    pending_comments = ledger.comments_pending()
    if pending_comments:
        problems.append({"kind": "ledger_comment_pending", "question_ids": [e["question_id"] for e in pending_comments]})
    age = last_run_age_minutes(now)
    if age is not None and age > STALE_RUN_MINUTES:
        problems.append({"kind": "no_recent_run", "minutes_since_last_run": age})

    return {"checked_at": now.isoformat(), "summary": summary, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_bot.monitor")
    parser.add_argument("--tournament", action="append", help="defaults to the run targets")
    args = parser.parse_args(argv)
    from agent_bot.cli import targets

    report = check(args.tournament or targets())
    print(json.dumps(report, indent=2))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
