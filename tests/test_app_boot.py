import hashlib
import json

from tests.conftest import REAL_DEVICE_JSON


def fingerprint_of(path):
    """Returns the sha256 of the file, or None when the file does not exist."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fresh_clone_config_base_serves_the_home_page(make_app):
    app = make_app()

    response = app.test_client().get("/")

    assert response.status_code == 200


def test_saving_the_plugin_order_writes_the_test_config_and_never_the_real_device_json(make_app, tmp_path):
    real_device_json_before = fingerprint_of(REAL_DEVICE_JSON)
    client = make_app().test_client()

    response = client.post("/api/plugin_order", json={"order": ["clock"]})

    written_config = json.loads((tmp_path / "device.json").read_text())
    assert response.status_code == 200
    assert written_config["plugin_order"] == ["clock"]
    assert fingerprint_of(REAL_DEVICE_JSON) == real_device_json_before
