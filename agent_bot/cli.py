"""Command line used by the forecasting agent.

    uv run python -m agent_bot.cli whoami
    uv run python -m agent_bot.cli pending --tournament minibench
    uv run python -m agent_bot.cli submit --tournament minibench --question-id 123 \
        --binary 0.31 --comment-file /path/reasoning.md
    uv run python -m agent_bot.cli retry-comments
    uv run python -m agent_bot.cli status
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dotenv

dotenv.load_dotenv()

from bot_helpers import silence_noisy_dependencies  # noqa: E402

silence_noisy_dependencies()

from forecasting_tools import (  # noqa: E402
    BinaryQuestion,
    DateQuestion,
    MetaculusClient,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericDistribution,
    NumericQuestion,
    Percentile,
)

from agent_bot import ledger, research  # noqa: E402
from agent_bot.forecast_format import (  # noqa: E402
    ForecastFormatError,
    check_percentiles,
    clip_binary,
    normalize_multiple_choice,
    validate_cdf,
)

PRACTICE_TOURNAMENTS = {
    "bot-testing-area",
    "32977",
    str(MetaculusClient.CURRENT_METACULUS_CUP_ID),
}
LIVE_FILE = Path(__file__).with_name("live_tournaments.json")


def live_tournaments() -> set[str]:
    return {str(t) for t in json.loads(LIVE_FILE.read_text())["live"]}


def is_practice(tournament: str) -> bool:
    return tournament in PRACTICE_TOURNAMENTS


def minutes_left(question: MetaculusQuestion) -> int | None:
    if question.close_time is None:
        return None
    return int((question.close_time - datetime.now(timezone.utc)).total_seconds() // 60)


def question_type(question: MetaculusQuestion) -> str:
    return getattr(question, "question_type", "unknown")


def details_markdown(question: MetaculusQuestion) -> str:
    lines = [
        question.give_question_details_as_markdown(),
        "",
        f"Question type: {question_type(question)}",
        f"Closes at (UTC): {question.close_time}",
        f"URL: {question.page_url}",
    ]
    if isinstance(question, (NumericQuestion, DateQuestion)):
        lines.append(f"Unit: {question.unit_of_measure}")
        lines.append(f"CDF points: {question.cdf_size}")
    if isinstance(question, DateQuestion):
        lines.append(f"Range: {question.lower_bound.date()} to {question.upper_bound.date()}")
        lines.append(f"Open lower bound: {question.open_lower_bound}; open upper bound: {question.open_upper_bound}")
    if isinstance(question, MultipleChoiceQuestion):
        lines.append("Options (use these labels exactly): " + json.dumps(question.options))
    lines += [
        "",
        "This question is OPEN and NOT resolved. Do not treat it as already decided.",
    ]
    return "\n".join(lines)


def open_questions(client: MetaculusClient, tournament: str) -> list[MetaculusQuestion]:
    target: str | int = int(tournament) if tournament.isdigit() else tournament
    return client.get_all_open_questions_from_tournament(target)


def cmd_whoami(args: argparse.Namespace) -> int:
    client = MetaculusClient()
    print(json.dumps({"user_id": client.get_current_user_id()}))
    return 0


def practice_settings() -> dict:
    path = ledger.state_dir() / "practice.json"
    settings = {"enabled": True, "targets": ["bot-testing-area"], "refresh_after_hours": 24.0, "max_per_run": 2}
    if path.exists():
        settings.update(json.loads(path.read_text()))
    return settings


def targets() -> list[str]:
    settings = practice_settings()
    practice = [t for t in settings["targets"] if is_practice(str(t))] if settings["enabled"] else []
    return [str(t) for t in practice] + sorted(live_tournaments())


def cmd_targets(args: argparse.Namespace) -> int:
    print(json.dumps({"targets": targets(), "live": sorted(live_tournaments())}))
    return 0


def select_questions(client: MetaculusClient, tournament: str) -> list[MetaculusQuestion]:
    questions = open_questions(client, tournament)
    if not is_practice(tournament):
        done = ledger.forecasted_question_ids()
        return [q for q in questions if not q.already_forecasted and q.id_of_question not in done]
    settings = practice_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=float(settings["refresh_after_hours"]))
    due = [q for q in questions if q.timestamp_of_my_last_forecast is None or q.timestamp_of_my_last_forecast < cutoff]
    due.sort(key=lambda q: q.timestamp_of_my_last_forecast or datetime.min.replace(tzinfo=timezone.utc))
    return due[: int(settings["max_per_run"])]


def cmd_pending(args: argparse.Namespace) -> int:
    client = MetaculusClient()
    questions_dir = ledger.state_dir() / "questions"
    questions_dir.mkdir(exist_ok=True)
    pending = []
    for question in select_questions(client, args.tournament):
        kind = question_type(question)
        detail_file = questions_dir / f"{question.id_of_question}.md"
        detail_file.write_text(details_markdown(question))
        brief = research.research_path(question.id_of_question)
        last_forecast = ledger.last_forecast_at(question.id_of_question)
        requested_at = ledger.research_requested_at(question.id_of_question)
        if requested_at and last_forecast and requested_at < last_forecast:
            requested_at = None
        pending.append(
            {
                "question_id": question.id_of_question,
                "post_id": question.id_of_post,
                "type": kind,
                "supported": kind in {"binary", "multiple_choice", "numeric", "discrete", "date"},
                "minutes_left": minutes_left(question),
                "title": question.question_text,
                "details_file": str(detail_file),
                "research_file": str(brief),
                "research_status": research.research_status(brief, not_before=last_forecast),
                "research_requested_at": requested_at.isoformat() if requested_at else None,
            }
        )
    pending.sort(key=lambda q: q["minutes_left"] if q["minutes_left"] is not None else 10**9)
    print(json.dumps({"tournament": args.tournament, "pending": pending}, indent=2))
    return 0


def cmd_research_request(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    items: list[research.ResearchItem] = []
    skipped: list[dict] = []
    for question_id in dict.fromkeys(args.question_id):
        details_file = ledger.state_dir() / "questions" / f"{question_id}.md"
        if not details_file.exists():
            skipped.append({"question_id": question_id, "reason": "no details file; run `pending` first"})
            continue
        details = details_file.read_text()
        close_line = next((l for l in details.splitlines() if l.startswith("Closes at (UTC): ")), "")
        close_value = close_line.removeprefix("Closes at (UTC): ").strip()
        close_time = datetime.fromisoformat(close_value) if close_value not in {"", "None"} else None
        deadline = research.research_deadline(now, close_time)
        if deadline <= now:
            skipped.append({"question_id": question_id, "reason": "closes too soon; forecast with your own research"})
            continue
        items.append(research.ResearchItem(question_id, details, research.research_path(question_id), close_time, deadline))
    if not items:
        print(json.dumps({"request_file": None, "skipped": skipped}))
        return 2
    request_file = research.research_dir() / "requests" / f"{now.strftime('%Y%m%dT%H%M%S')}.md"
    request_file.parent.mkdir(exist_ok=True)
    request_file.write_text(research.build_request(items))
    for item in items:
        ledger.append({"event": "research_requested", "question_id": item.question_id, "deadline": item.deadline.isoformat()})
    first_deadline = min(item.deadline for item in items).strftime("%Y-%m-%d %H:%M")
    print(json.dumps({
        "request_file": str(request_file),
        "message": (
            f"Research request for the Metaculus forecasting bot: read {request_file} and follow it exactly. "
            f"{len(items)} question(s); first brief due {first_deadline} UTC. Do not reply."
        ),
        "questions": [{"question_id": i.question_id, "research_file": str(i.output_path), "deadline": i.deadline.isoformat()} for i in items],
        "skipped": skipped,
    }))
    return 0


def build_payload(question: MetaculusQuestion, args: argparse.Namespace) -> tuple[str, object, str]:
    if isinstance(question, BinaryQuestion):
        if args.binary is None:
            raise ForecastFormatError("binary question: pass --binary")
        probability = clip_binary(args.binary)
        return "binary", probability, f"{probability:.1%}"
    if isinstance(question, MultipleChoiceQuestion):
        if args.mc is None:
            raise ForecastFormatError("multiple choice question: pass --mc")
        probabilities = normalize_multiple_choice(json.loads(args.mc), question.options)
        readable = ", ".join(f"{o}: {p:.1%}" for o, p in probabilities.items())
        return "multiple_choice", probabilities, readable
    if isinstance(question, (NumericQuestion, DateQuestion)):
        if args.percentiles is None:
            raise ForecastFormatError("numeric/discrete/date question: pass --percentiles")
        given = {float(k): v for k, v in json.loads(args.percentiles).items()}
        if isinstance(question, DateQuestion):
            raw = {k: datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc).timestamp() for k, v in given.items()}
        else:
            raw = {k: float(v) for k, v in given.items()}
        points = check_percentiles(raw)
        distribution = NumericDistribution.from_question(
            [Percentile(percentile=p, value=v) for p, v in points], question
        )
        cdf = [p.percentile for p in distribution.get_cdf()]
        validate_cdf(
            cdf,
            question.cdf_size - 1,
            question.open_lower_bound,
            question.open_upper_bound,
        )
        readable = ", ".join(f"P{round(level * 100)}={given[level]}" for level in sorted(given))
        return "numeric", cdf, readable
    raise ForecastFormatError(f"unsupported question type: {question_type(question)}")


def post_forecast(client: MetaculusClient, question: MetaculusQuestion, kind: str, payload: object) -> None:
    if kind == "binary":
        client.post_binary_question_prediction(question.id_of_question, payload)
    elif kind == "multiple_choice":
        client.post_multiple_choice_question_prediction(question.id_of_question, payload)
    else:
        client.post_numeric_question_prediction(question.id_of_question, payload)


def cmd_submit(args: argparse.Namespace) -> int:
    tournament = str(args.tournament)
    practice = is_practice(tournament)
    if not practice and tournament not in live_tournaments():
        print(f"REFUSED: {tournament} is a scored tournament and is not listed in agent_bot/live_tournaments.json (go-live not approved).")
        return 2
    reasoning = Path(args.comment_file).read_text().strip()
    if len(reasoning) < 200:
        print("REFUSED: the comment must explain the reasoning (at least 200 characters).")
        return 2

    client = MetaculusClient()
    with ledger.exclusive_lock():
        if not practice and args.question_id in ledger.forecasted_question_ids():
            print(f"REFUSED: question {args.question_id} already has a forecast in the ledger (one forecast per question).")
            return 2
        matches = [q for q in open_questions(client, tournament) if q.id_of_question == args.question_id]
        if not matches:
            print(f"REFUSED: question {args.question_id} is not open in {tournament}.")
            return 2
        question = matches[0]
        if question.already_forecasted and not practice:
            ledger.append({"event": "comment_posted", "question_id": args.question_id, "post_id": question.id_of_post, "tournament": tournament, "note": "already forecasted on Metaculus"})
            print(f"REFUSED: Metaculus already has a forecast from this bot on question {args.question_id}.")
            return 2

        try:
            kind, payload, readable = build_payload(question, args)
        except (ForecastFormatError, ValueError, json.JSONDecodeError) as error:
            print(f"FORMAT ERROR (nothing was posted): {error}")
            return 3

        comment = f"{reasoning}\n\n---\nFinal forecast: {readable}"
        comment_file = ledger.state_dir() / "comments" / f"{args.question_id}.md"
        comment_file.parent.mkdir(exist_ok=True)
        comment_file.write_text(comment)

        post_forecast(client, question, kind, payload)
        base = {"question_id": args.question_id, "post_id": question.id_of_post, "tournament": tournament, "type": kind, "comment_file": str(comment_file)}
        ledger.append({"event": "forecast_posted", **base, "forecast": payload})
        try:
            client.post_question_comment(question.id_of_post, comment, is_private=True)
        except Exception as error:  # forecast is already in; the comment is retried later
            ledger.append({"event": "comment_failed", **base, "error": str(error)[:300]})
            print(f"FORECAST POSTED, COMMENT FAILED (run retry-comments): {error}")
            return 4
        ledger.append({"event": "comment_posted", **base})
    print(f"OK: forecast and private comment posted on question {args.question_id} ({tournament}).")
    return 0


def cmd_retry_comments(args: argparse.Namespace) -> int:
    client = MetaculusClient()
    failures = 0
    with ledger.exclusive_lock():
        for entry in ledger.comments_pending():
            base = {k: entry[k] for k in ("question_id", "post_id", "tournament", "type", "comment_file")}
            try:
                client.post_question_comment(entry["post_id"], Path(entry["comment_file"]).read_text(), is_private=True)
                ledger.append({"event": "comment_posted", **base})
                print(f"comment posted on question {entry['question_id']}")
            except Exception as error:
                failures += 1
                ledger.append({"event": "comment_failed", **base, "error": str(error)[:300]})
                print(f"comment still failing on question {entry['question_id']}: {error}")
    return 1 if failures else 0


def cmd_wait_research(args: argparse.Namespace) -> int:
    import time

    deadline = time.monotonic() + args.timeout_minutes * 60
    while True:
        statuses = {
            qid: research.research_status(research.research_path(qid), not_before=ledger.last_forecast_at(qid))
            for qid in dict.fromkeys(args.question_id)
        }
        if all(s != "missing" for s in statuses.values()) or time.monotonic() >= deadline:
            print(json.dumps({"research_status": statuses}))
            return 0
        time.sleep(30)


def cmd_run_finished(args: argparse.Namespace) -> int:
    marker = ledger.state_dir() / "last_run.json"
    marker.write_text(json.dumps({"finished_at": datetime.now(timezone.utc).isoformat(), "note": args.note or ""}))
    print(f"run recorded in {marker}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    latest = ledger.latest_by_question()
    summary: dict[str, dict[str, int]] = {}
    for entry in latest.values():
        bucket = summary.setdefault(entry.get("tournament", "?"), {})
        bucket[entry["event"]] = bucket.get(entry["event"], 0) + 1
    print(json.dumps({"by_tournament": summary, "comments_pending": len(ledger.comments_pending())}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_bot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("whoami").set_defaults(func=cmd_whoami)
    pending = sub.add_parser("pending")
    pending.add_argument("--tournament", required=True)
    pending.set_defaults(func=cmd_pending)
    submit = sub.add_parser("submit")
    submit.add_argument("--tournament", required=True)
    submit.add_argument("--question-id", type=int, required=True)
    submit.add_argument("--comment-file", required=True)
    submit.add_argument("--binary", type=float)
    submit.add_argument("--mc", help='JSON object, e.g. {"Yes": 0.6, "No": 0.4}')
    submit.add_argument("--percentiles", help='JSON object level->value, e.g. {"0.05": 10, "0.5": 20, "0.95": 40}')
    submit.set_defaults(func=cmd_submit)
    research_request = sub.add_parser("research-request")
    research_request.add_argument("--question-id", type=int, action="append", required=True, help="repeat for a batch")
    research_request.set_defaults(func=cmd_research_request)
    sub.add_parser("retry-comments").set_defaults(func=cmd_retry_comments)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("targets").set_defaults(func=cmd_targets)
    wait_research = sub.add_parser("wait-research")
    wait_research.add_argument("--question-id", type=int, action="append", required=True)
    wait_research.add_argument("--timeout-minutes", type=float, default=12)
    wait_research.set_defaults(func=cmd_wait_research)
    run_finished = sub.add_parser("run-finished")
    run_finished.add_argument("--note")
    run_finished.set_defaults(func=cmd_run_finished)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
