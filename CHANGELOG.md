# Changelog

Significant changes to how the bot forecasts, kept for the prize inspection.

## 2026-11-22

- Forked `Metaculus/metac-bot-template`.
- Added agent mode: an autonomous LLM agent forecasts by following `AGENT_PLAYBOOK.md`; `agent_bot/cli.py` lists pending questions and submits with format validation (rules mirrored from Metaculus `questions/serializers/common.py`), one forecast per question, a private comment on every forecast, and a refusal to submit to scored tournaments not listed in `agent_bot/live_tournaments.json` (empty).
- Binary forecasts clipped to 3–97%, multiple-choice options floored at 2%, no extremizing.
- Playbook: added the official rule against creating markets or questions on other platforms to help the bot.
- Research hand-off: the forecasting agent can request an evidence brief from a separate research agent (`research-request`, `agent_bot/RESEARCH_PROTOCOL.md`). The research agent gives evidence only, no forecast; the forecasting agent verifies key facts, adds its own source and decides. If the brief is late, the forecasting agent forecasts with its own research.
