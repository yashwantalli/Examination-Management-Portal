#!/bin/sh
# Container start-up: migrate the database, make sure the admin exists, then serve.
set -e

echo "Applying database migrations..."
flask --app wsgi db upgrade

echo "Ensuring the admin account exists..."
flask --app wsgi init-db

echo "Starting gunicorn on port ${PORT:-8000}..."
exec gunicorn -c gunicorn.conf.py wsgi:app
