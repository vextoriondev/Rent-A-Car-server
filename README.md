# Django backend

Follow `../README.md` for setup, seed and tests. The API uses Django sessions and Django Ninja's automatic CSRF protection for cookie authentication. Only members of an organization can see its records; write routes require an appropriate staff role. Money is stored in `DecimalField` and JSON amounts are sent as decimal strings on the statement and write endpoints.

The notification outbox is durable; `rentals/tasks.py` deliberately records a delivery failure until a real provider adapter is configured. Never mark a message delivered just because a financial transaction was saved.
