"""Gunicorn settings. Everything can be overridden with environment variables."""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8000")
# One process with several threads keeps the in-memory login rate limiter exact (counters are
# per process). To run several worker processes, set EMP_RATELIMIT_STORAGE_URI to a Redis URL
# first, otherwise each worker counts separately and the effective limit is multiplied.
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 30
graceful_timeout = 20
keepalive = 5

# Log to stdout/stderr so Docker and hosting platforms collect everything.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("EMP_LOG_LEVEL", "info").lower()

# Containers: keep worker heartbeat files in memory, not on a slow overlay filesystem.
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None
