import json
import os
from pathlib import Path

import pytest
from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader
from PIL import Image

import config as config_module
from config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
CONFIG_BASE_DEVICE_JSON = REPO_ROOT / "install" / "config_base" / "device.json"
REAL_DEVICE_JSON = SRC_DIR / "config" / "device.json"


SECRET_ENVIRONMENT_NAME_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_URL", "_PASSWORD")


@pytest.fixture(autouse=True)
def isolate_tests_from_dotenv_and_environment(monkeypatch):
    """Keeps every test away from a real .env file and from secrets exported in the shell."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    secret_environment_names = [
        name for name in os.environ if name.endswith(SECRET_ENVIRONMENT_NAME_SUFFIXES)
    ]
    for name in secret_environment_names:
        monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def keep_device_files_inside_tmp_path(monkeypatch, tmp_path):
    """Points every path Config writes to (device.json, current image, plugin images) at tmp_path."""
    plugin_image_dir = tmp_path / "plugin_images"
    plugin_image_dir.mkdir()
    monkeypatch.setattr(Config, "config_file", str(tmp_path / "device.json"))
    monkeypatch.setattr(Config, "current_image_file", str(tmp_path / "current_image.png"))
    monkeypatch.setattr(Config, "plugin_image_dir", str(plugin_image_dir))


@pytest.fixture
def write_device_json(tmp_path):
    def write(content):
        device_json = tmp_path / "device.json"
        device_json.write_text(json.dumps(content))
        return device_json

    return write


@pytest.fixture
def make_app(write_device_json):
    """Builds the Flask app the way inkypi.py does, over a device.json copied from config_base."""
    from blueprints.apikeys import apikeys_bp
    from blueprints.main import main_bp
    from blueprints.playlist import playlist_bp
    from blueprints.plugin import plugin_bp
    from blueprints.settings import settings_bp

    def build(extra_config=None):
        config_base = json.loads(CONFIG_BASE_DEVICE_JSON.read_text())
        write_device_json({**config_base, **(extra_config or {})})
        device_config = Config()

        app = Flask("inkypi_test", root_path=str(SRC_DIR))
        app.jinja_loader = ChoiceLoader(
            [FileSystemLoader(str(SRC_DIR / "templates")), FileSystemLoader(str(SRC_DIR / "plugins"))]
        )
        app.config["DEVICE_CONFIG"] = device_config
        app.config["DISPLAY_MANAGER"] = None
        app.config["REFRESH_TASK"] = None
        for blueprint in (main_bp, settings_bp, plugin_bp, playlist_bp, apikeys_bp):
            app.register_blueprint(blueprint)
        return app

    return build


class FakeBrowserScreenshot:
    """Replaces take_screenshot_html: keeps the rendered HTML and returns a blank image of the asked size."""

    def __init__(self):
        self.rendered_html = ""

    def take(self, html_str, dimensions, timeout_ms=None):
        self.rendered_html = html_str
        return Image.new("RGB", tuple(dimensions), (255, 255, 255))


@pytest.fixture
def fake_browser_screenshot(monkeypatch):
    from plugins.base_plugin import base_plugin as base_plugin_module

    fake = FakeBrowserScreenshot()
    monkeypatch.setattr(base_plugin_module, "take_screenshot_html", fake.take)
    return fake
