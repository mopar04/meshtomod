from fastapi.testclient import TestClient

from nurbsmith import fixtures
from nurbsmith.api import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_tolerances():
    body = client.get("/api/tolerances").json()
    assert body["default"] == "functional"
    assert [lvl["key"] for lvl in body["levels"]] == ["rough", "functional", "precise"]


def test_upload_stl():
    data = fixtures.box().export(file_type="stl")
    res = client.post("/api/meshes", files={"file": ("box.stl", data)})
    assert res.status_code == 200
    assert res.json()["summary"]["watertight"] is True


def test_upload_bad_format():
    res = client.post("/api/meshes", files={"file": ("box.dwg", b"xx")})
    assert res.status_code == 422
