# Examination Management Portal (EMP)

[![CI](https://github.com/yashwantalli/empp/actions/workflows/ci.yml/badge.svg)](https://github.com/yashwantalli/empp/actions/workflows/ci.yml)

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
| Time | Booking windows use the wall clock of `EMP_TIMEZONE` (default `Asia/Kolkata`), so a UTC server behaves the same as a laptop |

## Run locally

```bash
git clone https://github.com/yashwantalli/empp.git
cd empp
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

## Configuration

| Variable | Purpose | Required in production |
|---|---|---|
| `EMP_ENV` | `development` or `production` | yes (`production`) |
| `EMP_SECRET_KEY` | Session signing key, 32+ random chars | yes |
| `DATABASE_URL` | e.g. `postgresql://user:pass@host/db` | yes |
| `EMP_ADMIN_EMAIL`, `EMP_ADMIN_PASSWORD`, `EMP_ADMIN_NAME` | Admin seeded on first start | password must not be the default |
| `EMP_TIMEZONE` | Timezone for booking windows | no |
| `EMP_MAIL_*` | Optional SMTP; without it, mails go to the in-app outbox | no |

## Tests

```bash
python -m pytest -q
```

CI runs the suite on Python 3.11 and 3.12 for every push and pull request.

## Roadmap

- [x] Environment-based config, production fail-fast, CI
- [ ] PostgreSQL + Flask-Migrate migrations
- [ ] gunicorn, Dockerfile, docker-compose, `/health` endpoint
- [ ] Login rate limiting, security headers, structured logging
- [ ] Deployed instance with auto-deploy from `main`
