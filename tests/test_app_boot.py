import json


def test_home_blueprint_on_config_base_serves_the_home_page(make_app):
    """Scope: the home blueprint over the config_base device.json; the real inkypi.py entry point is not booted."""
    app = make_app()

    response = app.test_client().get("/")

    assert response.status_code == 200


def test_saving_the_plugin_order_writes_the_test_config_and_never_the_real_device_json(
    make_app, tmp_path, real_files_access
):
    client = make_app().test_client()

    response = client.post("/api/plugin_order", json={"order": ["clock"]})

    written_config = json.loads((tmp_path / "device.json").read_text())
    assert response.status_code == 200
    assert written_config["plugin_order"] == ["clock"]
    assert real_files_access.attempts == []
