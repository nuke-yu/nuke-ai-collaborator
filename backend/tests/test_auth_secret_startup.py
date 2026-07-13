import os
import subprocess
import sys


def _import_auth(secret: str | None):
    env = os.environ.copy()
    env["NUKE_ENV"] = "production"
    if secret is None:
        env.pop("AUTH_SECRET", None)
    else:
        env["AUTH_SECRET"] = secret
    return subprocess.run(
        [sys.executable, "-c", "import core.auth"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_rejects_missing_auth_secret():
    result = _import_auth(None)
    assert result.returncode != 0
    assert "Refusing to start" in result.stderr


def test_production_rejects_short_auth_secret():
    result = _import_auth("too-short")
    assert result.returncode != 0
    assert "Refusing to start" in result.stderr


def test_production_accepts_strong_auth_secret():
    result = _import_auth("0123456789abcdef0123456789abcdef")
    assert result.returncode == 0, result.stderr
