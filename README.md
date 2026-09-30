# RentFlow full-stack starter

This ZIP gives you **separate `frontend/` and `backend/` projects** to use in your own repository. It expands the interactive Site prototype with a runnable Django Ninja accounting API. The existing published Site remains a browser-only demo; this ZIP is an independent copy for development.

## What runs

- `frontend/`: Next.js App Router, React, TypeScript, Tailwind and Radix/shadcn components. It has a local sample-data mode and an optional Django API mode.
- `backend/`: Django, Django Ninja, PostgreSQL models and migrations, organization membership, agreements, September obligations, expenses with approval and responsibility rules, partial payment allocations, balanced journal lines, statements, settlement close, and a notification outbox.
- `docker-compose.yml`: local PostgreSQL and Redis. Celery is configured, but no delivery provider is set; the worker records a failed delivery rather than claiming to send a real message.

### Local setup

Requirements: Python 3.11+ (developed against 3.11.9 and 3.12), Node.js 22+, Docker with Compose.

```bash
# Terminal 1, from the ZIP root
docker compose up -d
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# Edit .env. The example password is for local development only.
set -a; source .env; set +a  # On Windows, set these variables in your shell instead.
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo --username YOUR_USERNAME
python manage.py runserver 127.0.0.1:8000
```

Open `http://localhost:8000/admin/` and sign in. The seed command creates one worked September 2026 agreement with customer due ৳30,000, owner due ৳12,000, and driver due ৳5,000. API documentation is at `http://localhost:8000/api/v1/docs`.

```bash
# Terminal 2, from the ZIP root
cd frontend
npm ci
cp .env.example .env.local
# Set NEXT_PUBLIC_RENTFLOW_API_MODE=1 in .env.local
npm run dev
```

Open `http://localhost:3000`. Use **localhost** for both the admin login and frontend so the browser can send the Django session cookie through Next.js's same-origin `/backend/*` rewrite. Sign in through Django admin first, then reload the frontend. Use `NEXT_PUBLIC_RENTFLOW_API_MODE=0` for the browser-only demo without a running backend. If you use different domains in deployment, configure a deliberate cookie/origin/reverse-proxy policy; do not rely on the local rewrite as a complete production authentication design.

On Windows, use an environment loader of your choice or set each backend variable from `.env.example` in PowerShell. For a fast API test without Docker, set `USE_SQLITE=1` and `DJANGO_DEBUG=1`; SQLite is a development fallback and does not prove PostgreSQL row-lock behavior.

## Connected flows

In API mode, the dashboard loads the September snapshot from Django. Creating an agreement, issuing its September obligations, recording an expense or payment, approving/reversing an expense, retrying a notice, and closing a settlement call Django endpoints. The server enforces role and organization scope, rules, idempotency, amount limits, and atomic journal posting. The frontend displays recalculated snapshots after each successful call.

The frontend's actor screen is a **staff-only preview**, not an authenticated customer/owner/driver portal. File input currently keeps only the filename; it does not upload a receipt. Reports export the scoped browser snapshot. No real payment or notification is transmitted. The interface is fixed to September 2026 and uses whole BDT, while the backend stores two-decimal Decimal amounts.

## API and domain files

- `backend/rentals/models.py`: parties, fleet, agreements, periods, obligations, expenses, payments/allocations, offset, journal, outbox, and idempotency records.
- `backend/rentals/services.py`: transaction-safe issue, post, approve, reverse, pay, statement, and close operations.
- `backend/rentals/api.py`: versioned Django Ninja routes and role checks.
- `backend/rentals/migrations/`: committed initial schema and follow-up migrations.
- `backend/rentals/tests.py`: worked month, partial payment, retry, close and role-boundary tests.
- `frontend/lib/demo.ts`: local sample data and presentation-only figures.
- `frontend/lib/api.ts`: same-origin session/CSRF client and live snapshot.

Every money-changing POST requires an `Idempotency-Key` header. Posted expenses are corrected through linked reversing journal lines and a stored reason. The backend derives dues from obligations, allocations, and authorized offsets. The frontend's `statement()` function is for presentation only; treat `GET /periods/{id}/statement` as the backend's authoritative projection.

## Verify

```bash
cd backend
python manage.py test rentals
python manage.py check
cd ../frontend
npm run typecheck
npm run build
```

On Windows, using the checked-in `backend/venv`: `USE_SQLITE=1 DJANGO_DEBUG=1 venv/Scripts/python.exe manage.py test rentals` runs the suite in a couple of seconds without Docker. SQLite ignores `select_for_update`, so the row-locking concurrency test in `rentals/tests/test_concurrency.py` is skipped in that mode; run it for real against PostgreSQL (`docker compose up -d` at the project root, env from `.env.example`, or any Postgres reachable via `DATABASE_URL`) before trusting the concurrency guarantees.

## Work still required for production

The wireframe is larger than this first vertical slice. Add effective-dated agreement versions and full expense rule matrix; invoice documents and multi-invoice allocation UI; bank reconciliation; private receipt storage; real SMS/email/WhatsApp adapters and consent; actor authentication and object-scoped portals; owner/driver direct-payment cases; accounting review for tax, reversals, opening balances and closed-period adjustments; localization; and PostgreSQL concurrency tests. Do not put real business data into the published demo or treat the browser-calculated figures as accounting records.
