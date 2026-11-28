# Changelog

Significant changes to how the bot forecasts, kept for the prize inspection.

## 2026-11-24

- Go-live approved by the owner for MiniBench only: `agent_bot/live_tournaments.json` now lists `minibench`. The `minibench` slug is a recurring question series, so this covers the 30 Nov round and later rounds under the same slug. `fall-futureeval-2026` stays unapproved. The current MiniBench round had no open questions at approval time, so nothing was submitted by enabling it.

## 2026-11-22

- Forked `Metaculus/metac-bot-template`.
- Added agent mode: an autonomous LLM agent forecasts by following `AGENT_PLAYBOOK.md`; `agent_bot/cli.py` lists pending questions and submits with format validation (rules mirrored from Metaculus `questions/serializers/common.py`), one forecast per question, a private comment on every forecast, and a refusal to submit to scored tournaments not listed in `agent_bot/live_tournaments.json` (empty).
- Binary forecasts clipped to 3–97%, multiple-choice options floored at 2%, no extremizing.
- Playbook: added the official rule against creating markets or questions on other platforms to help the bot.
- Research hand-off: the forecasting agent can request an evidence brief from a separate research agent (`research-request`, `agent_bot/RESEARCH_PROTOCOL.md`). The research agent gives evidence only, no forecast; the forecasting agent verifies key facts, adds its own source and decides. If the brief is late, the forecasting agent forecasts with its own research.
- Research requests are batched (one message, earliest deadline first, per-question deadline and close time). The protocol now says the research step must not follow the forecasting playbook and must not add its own estimates; the forecasting agent ignores any it finds. Prompted by a timing test where the research step wrote practice forecasts.
- Scheduled runs: `targets` (practice targets plus live tournaments), practice re-forecasting limited to a few questions per run once a day, `wait-research`, `run-finished`, and `agent_bot/monitor.py`, which reports only problems (missed questions, forecasts without a private comment, open questions closing soon without a forecast, stale runs) and reads presence only, never forecast values.

## 2026-11-28

- Playbook updates from sourced evidence (research brief `metaculus-bot-research/strategy-001.md`), before any scored round starts: (1) never conclude an event has already happened without fresh dated evidence — bots have lost large scores by violating Metaculus's "assume it has not happened yet" assumption, and by mishandling "before date X" deadlines; (2) explicit base rate fixed BEFORE the inside view, with the inside view as a bounded adjustment (bot-maker advice, Spring 2026); (3) a final coherence pass using all inputs at once, since survey data shows winners average far more reasoning passes per question than non-winners; (4) betting odds / market prices / polling aggregates added as one input among several where they exist; (5) stock-ticker binary questions treated as near-coin-flips absent strong evidence (MiniBench retrospective: ~50% of binaries are random tickers). No extremization, no logit-space aggregation changes, no crowd-blending: evidence shows complexity without backtesting hurt bots that added it. Not tested on open tournament questions.
