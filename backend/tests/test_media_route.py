import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
from fastapi.testclient import TestClient

import skills.constants as _const
from main import app
from core import media
from workspace import layout


@pytest.fixture
def ws_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        orig = _const.WORKSPACE_ROOT
        _const.WORKSPACE_ROOT = root
        yield root
        _const.WORKSPACE_ROOT = orig


@pytest.fixture
def client():
    return TestClient(app)


def _seed(gid, kind, name, data=b"PNGDATA"):
    d = layout.group_media_dir(gid, kind)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)


def test_valid_signature_serves_file(client, ws_root):
    _seed(7, "screenshots", "a.png", b"hello")
    url = media.sign(7, "screenshots", "a.png")
    res = client.get(url)
    assert res.status_code == 200
    assert res.content == b"hello"


def test_bad_signature_rejected(client, ws_root):
    _seed(7, "screenshots", "a.png")
    res = client.get("/media/7/screenshots/a.png?exp=9999999999&sig=forged")
    assert res.status_code == 403


def test_expired_signature_rejected(client, ws_root):
    _seed(7, "uploads", "a.png")
    url = media.sign(7, "uploads", "a.png", ttl=-1)
    assert client.get(url).status_code == 403


def test_signature_is_group_scoped(client, ws_root):
    # A valid signature for group 7 must not grant access to group 8's path.
    _seed(8, "screenshots", "secret.png", b"other-group")
    url = media.sign(7, "screenshots", "secret.png")  # signed for gid 7
    q = parse_qs(urlparse(url).query)
    res = client.get(f"/media/8/screenshots/secret.png?exp={q['exp'][0]}&sig={q['sig'][0]}")
    assert res.status_code == 403


def test_path_traversal_blocked(client, ws_root):
    res = client.get("/media/7/uploads/..%2f..%2fsecret.png?exp=9999999999&sig=x")
    assert res.status_code in (403, 404)
