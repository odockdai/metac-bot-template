"""Agent mode: an autonomous LLM agent forecasts, this package guards and submits.

The agent reads open questions with `pending`, does its own research, and hands
each final forecast to `submit`, which validates the format, enforces one
forecast per question, refuses scored tournaments that are not switched on in
`live_tournaments.json`, and posts the forecast plus a private comment.
"""
