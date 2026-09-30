# RentFlow backend (Django + Django Ninja)

## In-progress work: read this first

The full financial domain model (identities, effective-dated agreements, expense rules, ledger, payments, settlement, notification delivery, documents/audit, reports, actor portal) is planned as milestones M0–M11.
Plan and progress checklist: `docs/BACKEND_MODEL_PLAN.md`.

- Resume at the first unchecked milestone; tick it (with notes) when it lands and passes its definition of done.
- Never edit an applied migration; add new ones. Keep the frontend contract in that file intact (or change frontend and backend together).
- Money is `Decimal`; money-changing actions go through `rentals/services` (atomic, idempotent, locked), never editable CRUD.
- Worked-example invariants must never regress: customer due 30,000; owner due 12,000; driver due 5,000; accrual margin 20,000; cash movement 7,000.

## Run

From `backend/`: `USE_SQLITE=1 DJANGO_DEBUG=1 venv/Scripts/python.exe manage.py test rentals` (Windows venv is `backend/venv`, Python 3.11). SQLite ignores row locks; concurrency tests need PostgreSQL (`docker compose up -d` at the project root, env from `.env.example`, or a `DATABASE_URL` in `backend/.env` pointing at Neon or any other Postgres). Against a pooled connection (Neon), add `--keepdb` — the pooler can leave a session or two attached right when the run ends, which makes Django's normal `DROP DATABASE test_<name>` teardown error out; `--keepdb` skips that and reuses the same test database next run. Full setup: `../README.md`.
