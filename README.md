# Examination Management Portal (EMP)

[![CI](https://github.com/yashwantalli/Examination-Management-Portal/actions/workflows/ci.yml/badge.svg)](https://github.com/yashwantalli/Examination-Management-Portal/actions/workflows/ci.yml)

A role-based portal for scheduling and evaluating viva/practical examinations, built with
**Flask, SQLAlchemy, Flask-Login and Bootstrap 5**. Admins set up courses, exams and rubrics;
examiners publish slots and score students; students book slots and see published results.

## Features

- **Admin:** approve examiners, manage courses/exams/weighted rubrics, set booking windows,
  reschedule bookings, reassign examiners, publish results.
- **Examiner:** create slots, view booked students, score each rubric criterion, track pending evaluations.
- **Student:** search exams, book/cancel slots, view history, download published scorecards as PDF.
- **Audit trail and API:** every booking action is logged (`BookingEvent`); a session-authenticated
  JSON API lives under `/api/v1` (spec in `api.yaml`).

## Design decisions worth knowing

| Concern | Approach |
|---|---|
| Overbooking | Seat taken with one atomic `UPDATE ... WHERE available_seats > 0`; if 0 rows change, someone else got it |
| Duplicate bookings | Partial unique index (`uq_active_booking_per_exam`) enforced by the database, not just app code |
| Business rules | Live in `services.py`, shared by HTML routes and the API |
| Config and secrets | Environment variables only. The app **refuses to start** in production with the dev secret, the default admin password or SQLite |
| Database | PostgreSQL in production (SQLite for local development); schema versioned with Alembic migrations |
| Concurrency | Proven by tests that fire many simultaneous booking requests at PostgreSQL |
| Time | Booking windows use the wall clock of `EMP_TIMEZONE` (default `Asia/Kolkata`), so a UTC server behaves the same as a laptop |

## Run locally

```bash
git clone https://github.com/yashwantalli/Examination-Management-Portal.git
cd Examination-Management-Portal
python3 -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # optional; defaults work for development
flask --app app seed-demo --reset                      # demo data (development only)
python app.py                                          # http://127.0.0.1:5000
```

Demo accounts (created by `seed-demo`, development only):

| Role | Email | Password |
|---|---|---|
| Admin | `admin@emp.local` | `Admin@123` |
| Examiner | `kavya.rao@emp.local` | `Demo@1234` |
| Student | `aarav@student.emp.local` | `Demo@1234` |

## Run with Docker (PostgreSQL, production mode)

```bash
cp .env.example .env     # then edit: set EMP_SECRET_KEY, EMP_ADMIN_PASSWORD, POSTGRES_PASSWORD
docker compose up --build
```

Open http://localhost:8000 and sign in with `EMP_ADMIN_EMAIL` / `EMP_ADMIN_PASSWORD`.
On every start the container applies pending migrations, makes sure the admin exists, and
starts gunicorn. Data lives in the `pgdata` volume; `docker compose down -v` deletes it.

Generate a secret key with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Use letters and digits only in `POSTGRES_PASSWORD` (it is embedded in a URL).

## Database migrations

Development and tests create tables automatically. Production (`EMP_ENV=production`) uses
Alembic migrations instead, so the schema is versioned and reproducible:

```bash
flask --app app db upgrade                     # apply migrations
flask --app app db migrate -m "describe change"   # after editing models.py
flask --app app db check                       # fails if models and migrations disagree
```

Commit the generated file in `migrations/versions/`. CI runs `db upgrade` on an empty
PostgreSQL database and `db check` on every push.

## Operations

| Topic | Behaviour |
|---|---|
| Health | `GET /health` returns `{"status":"ok"}` (200) or `{"status":"error"}` (503) after a `SELECT 1`. No login needed, no details leaked. Point your host's health check here. |
| Logging | Plain text to stdout. Failed and successful logins are logged (never passwords). Level via `EMP_LOG_LEVEL`. |
| Security headers | Content-Security-Policy (no inline scripts or external assets), `X-Frame-Options`, `nosniff`, `Referrer-Policy`, HSTS over https. |
| Rate limiting | Login: 10/minute and 60/hour per IP. Registration: 10/hour per IP. Over the limit returns 429. |
| Behind a proxy | Set `EMP_TRUST_PROXY=1` on Render/Railway/nginx so the real client IP and https are seen. Without it every visitor looks like the proxy and shares one limit. |
| Scaling out | Counters live in memory, per process, so the default is 1 worker x 4 threads. For more workers or instances set `EMP_RATELIMIT_STORAGE_URI=redis://...` first. |

## Configuration

| Variable | Purpose | Required in production |
|---|---|---|
| `EMP_ENV` | `development` or `production` | yes (`production`) |
| `EMP_SECRET_KEY` | Session signing key, 32+ random chars | yes |
| `DATABASE_URL` | e.g. `postgresql://user:pass@host/db` (rewritten to the psycopg 3 driver) | yes |
| `EMP_ADMIN_EMAIL`, `EMP_ADMIN_PASSWORD`, `EMP_ADMIN_NAME` | Admin seeded on first start | password must not be the default |
| `EMP_TIMEZONE` | Timezone for booking windows | no |
| `EMP_AUTO_CREATE_TABLES` | `1` builds tables at start (default in development); `0` expects migrations (default in production) | no |
| `EMP_TRUST_PROXY` | `1` behind a reverse proxy (see Operations) | yes, on a host |
| `EMP_COOKIE_SECURE` | `0` only to run the production stack over plain `http://localhost` | no |
| `EMP_RATELIMIT_STORAGE_URI` | `memory://` (default) or a Redis URL | no |
| `EMP_LOG_LEVEL` | `INFO` (default), `DEBUG`, ... | no |
| `PORT`, `WEB_CONCURRENCY`, `GUNICORN_THREADS` | Gunicorn bind port, processes (default 1), threads (default 4) | no |
| `POSTGRES_PASSWORD` | Used by docker compose only | compose |
| `EMP_MAIL_*` | Optional SMTP; without it, mails go to the in-app outbox | no |

## Tests

```bash
python -m pytest -q                       # fast: SQLite in memory; Postgres-only tests are skipped
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/emp_test python -m pytest -q   # everything
```

The PostgreSQL run includes concurrency tests: threads released at the same instant race for
the last seat. They were checked against the old read-then-write logic, where every racer got
the seat, so they genuinely detect overbooking.

CI runs the suite on SQLite (Python 3.11 and 3.12), on PostgreSQL 16 with migrations, and
builds and boots the Docker stack on every push and pull request.

## Roadmap

- [x] Environment-based config, production fail-fast, CI
- [x] PostgreSQL support and Alembic migrations
- [x] gunicorn, Dockerfile, docker-compose, `/health` endpoint
- [x] Login rate limiting, security headers, structured logging
- [ ] Deployed instance with auto-deploy from `main`
