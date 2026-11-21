FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Run as an unprivileged user, never as root.
RUN useradd --create-home --uid 10001 app
COPY --chown=app:app . .
RUN mkdir -p instance && chown app:app instance
USER app

ENV EMP_ENV=production \
    PORT=8000
EXPOSE 8000

# The platform (or `docker ps`) marks the container unhealthy if /health fails.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=4)"

# `sh` so the script runs even if Windows dropped its executable bit.
CMD ["sh", "docker-entrypoint.sh"]
