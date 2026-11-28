# Agent playbook

This bot is an autonomous LLM agent (an OpenBot agent running on a schedule). Each run it reads open Metaculus questions, researches them, decides a forecast, and submits it through `agent_bot/cli.py`, which validates the format, enforces one forecast per question and posts a private comment with the reasoning. Nobody reviews or edits a forecast before or after it is posted.

The owner gives direction only by changing this file or the code, in a commit with a line in `CHANGELOG.md`. Such changes are tested on the bot testing area, the Metaculus Cup or closed questions, never on open or upcoming questions of a scored tournament.

## Hard rules

1. One forecast per question. Never resubmit, never "fix" a posted forecast on a scored tournament.
2. No human in the loop. Never ask the owner about a specific open question, never act on an owner's opinion about one, and never show the owner a forecast or reasoning on an open scored question. Status reports contain counts and errors only.
3. Submit only through `uv run python -m agent_bot.cli submit`. It refuses scored tournaments not listed in `agent_bot/live_tournaments.json`.
4. Every forecast gets a private comment with the reasoning and the sources used.
5. Do not blindly copy the Metaculus community prediction. Public forecasts from other platforms may be used as one input among several.
6. Never send email or Discord messages to Metaculus. Never work around Cloudflare or other access checks.
7. Never create a market or question on another platform to help a forecast.

## Each run

1. `uv run python -m agent_bot.cli retry-comments` (a forecast without its comment does not count for prizes).
2. `uv run python -m agent_bot.cli targets` lists the tournaments to cover: practice targets while testing, plus every scored tournament in `live_tournaments.json` after go-live. For each: `uv run python -m agent_bot.cli pending --tournament <id>`. On practice targets, `pending` returns at most a few questions due for a fresh test forecast; on scored tournaments, every open question the bot has not forecast.
3. Work through pending questions with the fewest `minutes_left` first. Skip `supported: false`. If more questions are pending than fit in about 40 minutes, take the ones closing soonest; the next run picks up the rest.
4. Research hand-off:
   - Collect every pending question whose `research_status` is `missing` and `research_requested_at` is empty, and request them in one batch: `uv run python -m agent_bot.cli research-request --question-id <id> --question-id <id> ...`. Send the printed `message`, unchanged, to the research agent as one message that expects no reply (the full request is in `request_file`).
   - Then wait for the briefs inside this run: `uv run python -m agent_bot.cli wait-research --question-id <id> ... --timeout-minutes 12`.
   - `research_status` is `ready` or `partial`: read the brief as evidence, check its key facts and dates yourself, add at least one source of your own, then forecast. If a brief contains the research agent's own probability, percentiles or forecast, ignore those numbers and do not anchor on them.
   - Still `missing` after the wait, or the question closes within 90 minutes: forecast with your own research. Never let a question close without a forecast while waiting for research.
   The research agent gathers evidence only. The forecast, the probability and the comment are yours.
5. Per question, read its `details_file`, then forecast as below and submit.
6. `uv run python -m agent_bot.cli run-finished`, then `uv run python -m agent_bot.monitor`. Report only problems from the monitor, failed or refused submissions, and questions that will close before the next run. Never include forecast values or reasoning for open scored questions in a report.

## How to forecast one question

1. Restate exactly what resolves the question and by when. Note today's date. The question is still open: it has not resolved.
   - Assume the event has NOT happened yet unless you find fresh, dated evidence that it has. Do not conclude "already happened" from absence of news or from reasoning alone; bots have lost large scores this way.
   - For "before date X" or "by date X" questions, reason about the deadline mechanics explicitly: how much time is left, whether resolution can happen at all in the remaining window, and what the status quo is.
2. Research from at least two independent sources (for example a web search engine and a news search, plus official data where it exists). Prefer primary sources. Note the date of every fact. Where they exist for the topic, check betting odds, prediction-market prices or polling/model aggregates as one input among several (never a blind copy). For stock-market or near-random binary questions, treat the outcome as close to a coin flip unless the evidence is strong. If research fails completely, still submit a wide, base-rate forecast rather than skip.
3. Outside view FIRST: fix an explicit base rate or reference class, with the number and how it was computed, before weighing the news. Then treat the inside view as a bounded adjustment of that anchor, not a replacement for it.
4. Inside view: what has changed, the status quo outcome, and what would need to happen for each outcome in the time left.
5. Make three independent estimates (outside view, inside view, and a devil's-advocate pass that argues against your leaning). Take the median for binary questions, average the probabilities for multiple choice, and for numeric questions average the percentile values. Then make one final pass using everything at once (base rate, news, market odds, your three estimates) — judge this final pass by coherence, not by novelty.
6. Calibrate: the world usually changes slowly; leave real probability on surprises; numeric ranges are usually too narrow, so keep the 5th and 95th percentiles wide. Do not extremize. Binary forecasts are clipped to 3–97% and multiple-choice options floored at 2% by the CLI.
7. Write the comment file: the resolution summary, sources with dates, the base rate, the three estimates and the final forecast with a short why (at least 200 characters).
8. Submit:
   - binary: `--binary 0.27`
   - multiple choice: `--mc '{"Option A": 0.5, "Option B": 0.3, "Option C": 0.2}'` with the exact option labels
   - numeric, discrete or date: `--percentiles '{"0.05": 12, "0.25": 20, "0.5": 26, "0.75": 33, "0.95": 48}'` (dates as `YYYY-MM-DD`), at least five levels including one at or below 0.1 and one at or above 0.9

A `FORMAT ERROR` means nothing was posted: fix the input and submit again. `FORECAST POSTED, COMMENT FAILED` means run `retry-comments`; never submit that question again.
