import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytz
from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader
from PIL import Image

import config as config_module
from config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
CONFIG_BASE_DEVICE_JSON = REPO_ROOT / "install" / "config_base" / "device.json"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

TEST_TIMEZONE = "Europe/Paris"
FROZEN_NOW = pytz.utc.localize(datetime(2026, 10, 13, 9, 0))
DEFAULT_RESOLUTION = [800, 480]
ICALENDAR_INSTANCE_NAME = "Agenda"


@pytest.fixture(autouse=True)
def isolate_tests_from_dotenv_and_environment(monkeypatch):
    """Keeps every test away from a real .env file and from a CALENDAR_URL exported in the shell."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("CALENDAR_URL", raising=False)


@pytest.fixture(autouse=True)
def keep_calendar_screenshot_out_of_the_checkout(monkeypatch, tmp_path):
    """Redirects src/calendar_temp.png to tmp_path; every other resolved path stays untouched."""
    from plugins.icalendar import icalendar as icalendar_module

    real_resolve_path = icalendar_module.resolve_path

    def resolve_path_with_tmp_screenshot(file_path):
        if file_path == "calendar_temp.png":
            return str(tmp_path / file_path)
        return real_resolve_path(file_path)

    monkeypatch.setattr(icalendar_module, "resolve_path", resolve_path_with_tmp_screenshot)


class FakeDeviceConfig:
    """Device config double whose environment comes from an injected dict."""

    def __init__(self, config, environment):
        self._config = config
        self._environment = environment

    def get_resolution(self):
        return tuple(self._config["resolution"])

    def get_config(self, key=None, default=None):
        return self._config.get(key, {} if default is None else default)

    def load_env_key(self, key):
        return self._environment.get(key)


@pytest.fixture
def make_device_config():
    def build(orientation="horizontal", environment=None):
        config = {
            "resolution": DEFAULT_RESOLUTION,
            "orientation": orientation,
            "timezone": TEST_TIMEZONE,
        }
        return FakeDeviceConfig(config, environment or {})

    return build


@pytest.fixture
def frozen_clock(monkeypatch):
    """Freezes datetime.now() as seen by the icalendar plugin."""
    from plugins.icalendar import icalendar as icalendar_module

    class AnyDatetimeMeta(type):
        def __instancecheck__(cls, instance):
            return isinstance(instance, datetime)

    class FrozenDatetime(datetime, metaclass=AnyDatetimeMeta):
        @classmethod
        def now(cls, tz=None):
            return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW

    monkeypatch.setattr(icalendar_module, "datetime", FrozenDatetime)
    return FROZEN_NOW


class FakeCalendarNetwork:
    """Replaces urllib.request.urlopen: records calls, serves an ICS or fails."""

    def __init__(self):
        self.requested_urls = []
        self._ics_bytes = None
        self._failure_for_url = None

    def serve_ics(self, ics_bytes):
        self._ics_bytes = ics_bytes

    def fail_with(self, failure_for_url):
        self._failure_for_url = failure_for_url

    def urlopen(self, url, *args, **kwargs):
        self.requested_urls.append(url)
        if self._failure_for_url:
            raise self._failure_for_url(url)
        if self._ics_bytes is None:
            raise AssertionError("unexpected network call")
        return SimpleNamespace(read=lambda: self._ics_bytes)


@pytest.fixture
def calendar_network(monkeypatch):
    from plugins.icalendar import icalendar as icalendar_module

    network = FakeCalendarNetwork()
    monkeypatch.setattr(icalendar_module.urllib.request, "urlopen", network.urlopen)
    return network


class FakeChromium:
    """Replaces the chromium subprocess: keeps the HTML, writes a blank PNG."""

    def __init__(self):
        self.rendered_html = ""
        self.window_sizes = []

    def run(self, command, **kwargs):
        window_size = next(a for a in command if a.startswith("--window-size="))
        screenshot_path = next(a for a in command if a.startswith("--screenshot="))
        html_url = command[-1]

        width, height = (int(v) for v in window_size.split("=")[1].split(","))
        self.window_sizes.append((width, height))
        self.rendered_html = Path(html_url.removeprefix("file://")).read_text()
        Image.new("RGB", (width, height), (255, 255, 255)).save(
            screenshot_path.split("=", 1)[1], format="PNG"
        )
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")


@pytest.fixture
def fake_chromium(monkeypatch):
    from plugins.icalendar import icalendar as icalendar_module

    chromium = FakeChromium()
    monkeypatch.setattr(icalendar_module, "subprocess", SimpleNamespace(run=chromium.run))
    return chromium


@pytest.fixture
def dentiste_ics_bytes():
    return (FIXTURES_DIR / "dentiste.ics").read_bytes()


def device_json_with_icalendar_instance():
    return {
        "resolution": DEFAULT_RESOLUTION,
        "timezone": TEST_TIMEZONE,
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "icalendar",
                            "name": ICALENDAR_INSTANCE_NAME,
                            "plugin_settings": {"title": "Agenda", "viewMode": "list"},
                            "refresh": {"interval": 3600},
                        }
                    ],
                }
            ],
            "active_playlist": "Default",
        },
    }


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    """Builds a Flask app with the four blueprints over a tmp device.json."""
    from blueprints.main import main_bp
    from blueprints.playlist import playlist_bp
    from blueprints.plugin import plugin_bp
    from blueprints.settings import settings_bp
    from plugins.plugin_registry import load_plugins

    def build(extra_config=None):
        base_config = json.loads(CONFIG_BASE_DEVICE_JSON.read_text())
        device_json = tmp_path / "device.json"
        device_json.write_text(json.dumps({**base_config, **(extra_config or {})}))
        plugin_image_dir = tmp_path / "plugin_images"
        plugin_image_dir.mkdir(exist_ok=True)
        monkeypatch.setattr(Config, "config_file", str(device_json))
        monkeypatch.setattr(Config, "plugin_image_dir", str(plugin_image_dir))
        device_config = Config()
        load_plugins([p for p in device_config.get_plugins() if p["id"] == "icalendar"])

        app = Flask("inkypi_test", root_path=str(SRC_DIR))
        app.jinja_loader = ChoiceLoader(
            [FileSystemLoader(str(SRC_DIR / "templates")), FileSystemLoader(str(SRC_DIR / "plugins"))]
        )
        app.config["DEVICE_CONFIG"] = device_config
        app.config["DISPLAY_MANAGER"] = None
        app.config["REFRESH_TASK"] = None
        for blueprint in (main_bp, settings_bp, plugin_bp, playlist_bp):
            app.register_blueprint(blueprint)
        return app

    return build
