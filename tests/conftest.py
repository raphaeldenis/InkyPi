import builtins
import json
import os
import socket
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


ALLOWED_ENVIRONMENT_NAMES = frozenset({"PATH", "HOME", "LANG", "TMPDIR", "VIRTUAL_ENV"})
ALLOWED_ENVIRONMENT_NAME_PREFIXES = ("LC_", "PYTHON", "PYTEST_")


def is_allowed_environment_name(name):
    return name in ALLOWED_ENVIRONMENT_NAMES or name.startswith(ALLOWED_ENVIRONMENT_NAME_PREFIXES)


@pytest.fixture(autouse=True)
def isolate_tests_from_dotenv_and_environment(monkeypatch):
    """Keeps every test away from a real .env file and leaves it only the allowlisted shell variables.

    Tests that need a setting (weather, calendar) inject it explicitly with monkeypatch.setenv.
    """
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    names_outside_allowlist = [name for name in os.environ if not is_allowed_environment_name(name)]
    for name in names_outside_allowlist:
        monkeypatch.delenv(name)


class OutboundNetworkBlocked(AssertionError):
    pass


class OutboundNetworkGuard:
    """Refuses every outbound connection and name lookup except towards the explicitly allowed hosts."""

    def __init__(self):
        self._allowed_hosts = set()

    def allow(self, host):
        self._allowed_hosts.add(host)

    def check(self, host):
        if host not in self._allowed_hosts:
            raise OutboundNetworkBlocked(f"outbound network call to {host!r} blocked by the test suite")


def host_of(address):
    is_internet_address = isinstance(address, tuple) and bool(address)
    return address[0] if is_internet_address else address


@pytest.fixture(autouse=True)
def outbound_network_guard(monkeypatch):
    guard = OutboundNetworkGuard()
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock, address):
        guard.check(host_of(address))
        return real_connect(sock, address)

    def guarded_create_connection(address, *args, **kwargs):
        guard.check(host_of(address))
        return real_create_connection(address, *args, **kwargs)

    def guarded_getaddrinfo(host, *args, **kwargs):
        guard.check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    return guard


class RealFileAccessRecorder:
    """Refuses to open or stat the real .env and device.json and remembers every attempt."""

    def __init__(self, forbidden_paths):
        self.forbidden_paths = {os.path.abspath(path) for path in forbidden_paths}
        self.attempts = []

    def refuse_if_real(self, path):
        is_real_dotenv = isinstance(path, (str, os.PathLike)) and os.path.abspath(path) in self.forbidden_paths
        if is_real_dotenv:
            self.attempts.append(os.fspath(path))
            raise PermissionError(f"the test suite never touches a real secret file: {path}")


@pytest.fixture(autouse=True)
def real_files_access(monkeypatch, tmp_path):
    """Serves the API Keys blueprint from a disposable .env and records any access to the real .env or device.json."""
    from blueprints import apikeys as apikeys_module

    real_secret_file_paths = [REPO_ROOT / ".env", apikeys_module.get_env_path(), REAL_DEVICE_JSON]
    recorder = RealFileAccessRecorder(real_secret_file_paths)
    real_open = builtins.open
    real_exists = os.path.exists
    real_stat = os.stat

    def recording_open(file, *args, **kwargs):
        recorder.refuse_if_real(file)
        return real_open(file, *args, **kwargs)

    def recording_exists(path):
        recorder.refuse_if_real(path)
        return real_exists(path)

    def recording_stat(path, *args, **kwargs):
        recorder.refuse_if_real(path)
        return real_stat(path, *args, **kwargs)

    disposable_dotenv = tmp_path / "api_keys.env"
    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(os.path, "exists", recording_exists)
    monkeypatch.setattr(os, "stat", recording_stat)
    monkeypatch.setattr(apikeys_module, "get_env_path", lambda: str(disposable_dotenv))
    return recorder


@pytest.fixture
def disposable_dotenv(tmp_path):
    return tmp_path / "api_keys.env"


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
