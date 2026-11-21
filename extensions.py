"""Shared extension objects, created here so blueprints can import them without a cycle."""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Limits are attached per route (see routes/auth.py, routes/api.py); there is no global limit.
limiter = Limiter(key_func=get_remote_address)

LOGIN_LIMIT = "10 per minute;60 per hour"
REGISTER_LIMIT = "10 per hour"
