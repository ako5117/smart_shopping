# Contributing

Agreed at the 22 Sep 2026 team meeting.

## Branches

- `main` is always in a working state. Nobody pushes to it directly.
- All work happens on a branch named for what it does:
  - `feature/<short-name>` — new functionality (e.g. `feature/mpesa-stk-push`)
  - `fix/<short-name>` — bug fixes
  - `docs/<short-name>` — documentation only
- Keep branches short-lived; merge within days, not weeks.

## Pull requests

1. Push your branch and open a pull request into `main`.
2. Describe what changed and how you tested it.
3. At least one other team member reviews before merging.
4. Squash or merge once approved; delete the branch afterwards.

## Commits

- One logical change per commit.
- Message: short summary in the imperative ("Add STK Push callback handler"), then a blank line and detail if needed.

## Secrets

- Never commit API keys, passwords, or tokens. Each service has a `.env.example`; copy it to `.env` locally (`.env` is git-ignored).
- If a secret is committed by mistake, tell the team and rotate it — deleting the commit is not enough.

## Code

- Python services: include a `requirements.txt`, a `README.md` with run instructions, and tests under `tests/`.
- Run the tests before opening a pull request.
