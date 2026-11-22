# Changelog

Significant changes to how the bot forecasts, kept for the prize inspection.

## 2026-11-22

- Forked `Metaculus/metac-bot-template`.
- Added agent mode: an autonomous LLM agent forecasts by following `AGENT_PLAYBOOK.md`; `agent_bot/cli.py` lists pending questions and submits with format validation (rules mirrored from Metaculus `questions/serializers/common.py`), one forecast per question, a private comment on every forecast, and a refusal to submit to scored tournaments not listed in `agent_bot/live_tournaments.json` (empty).
- Binary forecasts clipped to 3–97%, multiple-choice options floored at 2%, no extremizing.
