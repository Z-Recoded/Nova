# Nova Skill: Financial

Version: 1.0 | Last updated: 2026-07-06

## Purpose

Orient any model invoked for a financial-domain task (reading/reasoning over nova_state.db financial/ tables) on schema, approved data sources, and the hard local-only privacy policy.

## Conventions

- Schema is exactly as defined in Nova Reference — nova_state.db Domain Schemas v1.0: financial_accounts, financial_bills, financial_transactions. Read that document before writing any query against these tables.
- Money is always INTEGER cents; never treat it as a float in any calculation — convert to display currency only at the final output step.
- Bills with recurrence = 'irregular' get a wider alert window (7 days) than fixed-schedule bills (2 days) — this is a fixed rule, not a per-task judgment call.
- Multi-currency totals are always reported per-currency; never sum across currencies without an explicit, flagged conversion step.
- Approved data sources: Marvin's linked spreadsheet export only. No live bank API polling exists in v1 — do not assume real-time balance data.

## Constraints

- Financial data never enters the Chroma vector corpus — it lives in nova_state.db only. This is a hard boundary, not a default: even a summary derived from financial data must not be written into a corpus-ingested note.
- Never fabricate a balance, transaction, or bill amount when source data is missing — report the gap (see confidence = 'estimated'/adapter_log) rather than guessing a plausible number.
- Never surface financial alerts outside the approved notification channels (Open WebUI, Tailscale push) — no external service should ever see financial figures.
- Never auto-categorize a transaction with high confidence language if the categorization model's actual confidence is low — say "likely" rather than asserting.

## Output format

A financial task response states: the figure or answer, the `as_of`/timestamp it reflects, and its confidence level ('confirmed'/'estimated'/'stale') if not fully confirmed. Alerts follow the system_pending_alerts message format.

## Examples

Good: "Chase Checking balance as of 2026-07-06 06:00 UTC: $3,420.50 (confirmed). Comcast Internet ($89.99) is due in 2 days with autopay disabled — flagging per the bill-due-soon alert rule."

Bad: "You've got about $3,400 in checking, should be fine for bills this month." (Rounds a precise figure into vague language, offers an unrequested and unverified judgment about sufficiency, and skips the confidence/as-of disclosure entirely — the kind of soft imprecision this skill file exists to prevent in a domain where exactness matters.)
