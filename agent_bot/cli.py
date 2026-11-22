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
from datetime import datetime, timezone
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

from agent_bot import ledger  # noqa: E402
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


def cmd_pending(args: argparse.Namespace) -> int:
    client = MetaculusClient()
    done = ledger.forecasted_question_ids()
    questions_dir = ledger.state_dir() / "questions"
    questions_dir.mkdir(exist_ok=True)
    pending = []
    for question in open_questions(client, args.tournament):
        if question.already_forecasted or question.id_of_question in done:
            continue
        kind = question_type(question)
        detail_file = questions_dir / f"{question.id_of_question}.md"
        detail_file.write_text(details_markdown(question))
        pending.append(
            {
                "question_id": question.id_of_question,
                "post_id": question.id_of_post,
                "type": kind,
                "supported": kind in {"binary", "multiple_choice", "numeric", "discrete", "date"},
                "minutes_left": minutes_left(question),
                "title": question.question_text,
                "details_file": str(detail_file),
            }
        )
    pending.sort(key=lambda q: q["minutes_left"] if q["minutes_left"] is not None else 10**9)
    print(json.dumps({"tournament": args.tournament, "pending": pending}, indent=2))
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
    sub.add_parser("retry-comments").set_defaults(func=cmd_retry_comments)
    sub.add_parser("status").set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
