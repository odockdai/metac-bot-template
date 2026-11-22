import os
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("AGENT_BOT_STATE_DIR", tempfile.mkdtemp())

from forecasting_tools import DateQuestion, DiscreteQuestion, NumericQuestion  # noqa: E402

from agent_bot import cli  # noqa: E402
from agent_bot.forecast_format import (  # noqa: E402
    ForecastFormatError,
    check_percentiles,
    clip_binary,
    normalize_multiple_choice,
    validate_cdf,
)


def numeric_question(cls=NumericQuestion, cdf_size=201, open_lower=True, open_upper=True):
    return cls(
        question_text="How many?",
        id_of_question=1,
        id_of_post=1,
        lower_bound=0.0,
        upper_bound=100.0,
        open_lower_bound=open_lower,
        open_upper_bound=open_upper,
        cdf_size=cdf_size,
    )


PERCENTILES = '{"0.05": 10, "0.25": 30, "0.5": 45, "0.75": 60, "0.95": 85}'


class FormatTests(unittest.TestCase):
    def test_binary_is_clipped(self):
        self.assertEqual(clip_binary(0.999), 0.97)
        self.assertEqual(clip_binary(0.0), 0.03)
        self.assertEqual(clip_binary(0.4), 0.4)
        with self.assertRaises(ForecastFormatError):
            clip_binary(1.5)

    def test_multiple_choice_floor_and_sum(self):
        result = normalize_multiple_choice({"A": 0.97, "B": 0.03, "C": 0.0}, ["A", "B", "C"])
        self.assertAlmostEqual(sum(result.values()), 1.0, places=6)
        self.assertGreaterEqual(min(result.values()), 0.02)
        with self.assertRaises(ForecastFormatError):
            normalize_multiple_choice({"A": 1.0}, ["A", "B"])

    def test_percentiles_need_tails_and_order(self):
        with self.assertRaises(ForecastFormatError):
            check_percentiles({0.25: 1, 0.5: 2, 0.75: 3, 0.8: 4, 0.85: 5})
        with self.assertRaises(ForecastFormatError):
            check_percentiles({0.05: 5, 0.25: 4, 0.5: 6, 0.75: 7, 0.95: 8})

    def test_numeric_payloads_pass_server_rules(self):
        cases = [
            numeric_question(),
            numeric_question(open_lower=False, open_upper=False),
            numeric_question(cls=DiscreteQuestion, cdf_size=11, open_lower=False, open_upper=True),
        ]
        for question in cases:
            args = Namespace(binary=None, mc=None, percentiles=PERCENTILES)
            kind, cdf, _ = cli.build_payload(question, args)
            self.assertEqual(kind, "numeric")
            validate_cdf(cdf, question.cdf_size - 1, question.open_lower_bound, question.open_upper_bound)

    def test_date_payload(self):
        start = datetime(2027, 1, 1, tzinfo=timezone.utc)
        question = DateQuestion(
            question_text="When?",
            id_of_question=2,
            id_of_post=2,
            lower_bound=start,
            upper_bound=start + timedelta(days=365),
            open_lower_bound=True,
            open_upper_bound=True,
        )
        percentiles = '{"0.05": "2027-01-20", "0.25": "2027-03-01", "0.5": "2027-05-01", "0.75": "2027-08-01", "0.95": "2027-12-01"}'
        kind, cdf, _ = cli.build_payload(question, Namespace(binary=None, mc=None, percentiles=percentiles))
        validate_cdf(cdf, 200, True, True)


class GuardTests(unittest.TestCase):
    def test_scored_tournament_refused_until_live(self):
        comment = Path(tempfile.mkstemp(suffix=".md")[1])
        comment.write_text("x" * 300)
        for tournament in ("minibench", "fall-futureeval-2026", "33121"):
            args = Namespace(tournament=tournament, question_id=1, comment_file=str(comment), binary=0.5, mc=None, percentiles=None)
            self.assertEqual(cli.cmd_submit(args), 2)

    def test_short_comment_refused(self):
        comment = Path(tempfile.mkstemp(suffix=".md")[1])
        comment.write_text("too short")
        args = Namespace(tournament="bot-testing-area", question_id=1, comment_file=str(comment), binary=0.5, mc=None, percentiles=None)
        self.assertEqual(cli.cmd_submit(args), 2)


if __name__ == "__main__":
    unittest.main()
