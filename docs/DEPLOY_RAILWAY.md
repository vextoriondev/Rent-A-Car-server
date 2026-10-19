# Deploying the backend to Railway

The frontend is on Vercel (`https://rentflow-one-ashy.vercel.app`). The database is Neon. This file puts the Django backend on Railway.
Railway reads `railway.json` and `.python-version` from the repository root, so push the `Rent-A-Car-server` folder as its own repository (or set the service's Root Directory to it).

What `railway.json` does:
- Build: installs `requirements.txt`, then `collectstatic` (the admin's CSS, served by WhiteNoise).
- Before each deploy: `python manage.py migrate --noinput` against `DATABASE_URL`.
- Start: `gunicorn config.wsgi` with 3 workers on `$PORT`.
- Health check: `/healthz` must answer `ok` before traffic moves to a new version.

## Steps
1. railway.com > **New Project > Deploy from GitHub repo** > pick the backend repository.
2. Open the service > **Variables** and add all of these **before** the first deploy (the build needs the secret key):

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | a new long random string. Generate one: `python -c "import secrets;print(secrets.token_urlsafe(60))"` |
| `DJANGO_DEBUG` | `0` |
| `DATABASE_URL` | the Neon connection string. Check the host and database in it first: **migrations run on it at every deploy** |
| `DJANGO_ALLOWED_HOSTS` | leave empty or `localhost`. The Railway public domain and the health check host are added automatically |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://rentflow-one-ashy.vercel.app` (add a custom domain too, comma-separated) |
| `PRIVATE_MEDIA_ROOT` | `/data/private_media` (see step 4) |

3. Service > **Settings > Networking > Generate Domain**. Note the address, e.g. `https://rentflow-api.up.railway.app`.
4. Uploads (receipts and documents) are files on disk. Add a **Volume** (service > right-click canvas > Volume, or Settings > Volumes) mounted at `/data`, so they survive a redeploy. Without it every upload is lost on the next deploy.
5. Redeploy. Open `https://<railway-domain>/healthz` (should show `ok`) and `https://<railway-domain>/api/v1/docs`.
6. In **Vercel** > project > Settings > Environment Variables: set `BACKEND_URL` to the Railway address (no trailing slash, no `/api/v1`), then **redeploy** the frontend.
7. Open `https://rentflow-one-ashy.vercel.app/register` and create the first organisation and admin, or create an admin with `railway run python manage.py createsuperuser` and sign in.

## If something fails
- Build says "Set DJANGO_SECRET_KEY for production": the variable is missing or was added after the build; add it and redeploy.
- Health check fails: look at the deploy logs. A database error means `DATABASE_URL` is wrong or Neon is asleep (it wakes in a few seconds; redeploy).
- Login on the Vercel site gives 403 CSRF: the Vercel address is missing from `DJANGO_CSRF_TRUSTED_ORIGINS`.
- Login gives 400 from the backend: the host is not allowed. Set `DJANGO_ALLOWED_HOSTS` to the Railway domain.

## Not included
- Celery and Redis (queued messages and reminders) are not started. They are deferred with the SMS and email work (M7).
- The registration page is open to anyone who finds the site. Each registration creates a separate organisation, but decide whether you want that on a public address.
