import os
import subprocess
import sys

import pytest

import config
from config import Config, validate_config


def _prod(**over):
    cfg = {"IS_PRODUCTION": True, "SECRET_KEY": "x" * 48, "ADMIN_PASSWORD": "Str0ng-and-unique!",
           "SQLALCHEMY_DATABASE_URI": "postgresql://u:p@h/db"}
    cfg.update(over)
    return cfg


def test_valid_production_config_passes():
    validate_config(_prod())


@pytest.mark.parametrize("override", [
    {"SECRET_KEY": config.DEV_SECRET},
    {"SECRET_KEY": "short"},
    {"ADMIN_PASSWORD": config.DEV_ADMIN_PASSWORD},
    {"SQLALCHEMY_DATABASE_URI": "sqlite:///x.db"},
])
def test_unsafe_production_config_is_rejected(override):
    with pytest.raises(RuntimeError):
        validate_config(_prod(**override))


def test_development_config_is_not_validated():
    validate_config({"IS_PRODUCTION": False, "SECRET_KEY": config.DEV_SECRET})


def test_legacy_postgres_scheme_is_rewritten(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    assert config._database_uri() == "postgresql://u:p@h:5432/db"


def test_local_now_follows_configured_timezone():
    code = ("from models import local_now; from datetime import datetime, timezone; "
            "import sys; d=local_now()-datetime.now(timezone.utc).replace(tzinfo=None); "
            "print(round(d.total_seconds()/60))")
    env = dict(os.environ, TZ="UTC", EMP_TIMEZONE="Asia/Kolkata")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(__file__)))
    assert out.stdout.strip() in ("330", "331"), out.stderr  # UTC+5:30
