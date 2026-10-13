import logging
import time

import pytest
from PIL import Image

from config import Config
from display.display_manager import DisplayManager
from plugins.plugin_registry import load_plugins
from refresh_task import RefreshTask

POLL_TIMEOUT_SECONDS = 10
POLL_PERIOD_SECONDS = 0.05


def wait_until(condition):
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(POLL_PERIOD_SECONDS)
    return False


@pytest.fixture
def screen_output_dir(tmp_path):
    output_dir = tmp_path / "screen"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def previous_screen(screen_output_dir, tmp_path):
    """Writes the screen a previous run left behind and returns {path: bytes}."""
    previous_files = {
        tmp_path / "current_image.png": (12, 34, 56),
        screen_output_dir / "latest.png": (78, 90, 12),
        screen_output_dir / "display_20200101_000000.png": (34, 56, 78),
    }
    for path, color in previous_files.items():
        Image.new("RGB", (800, 480), color).save(path, "PNG")
    return {path: path.read_bytes() for path in previous_files}


@pytest.fixture
def started_refresh_task(write_device_json, screen_output_dir):
    """Runs the real refresh cycle over a year_progress playlist and a mock display."""
    write_device_json(
        {
            "name": "InkyPi test",
            "display_type": "mock",
            "output_dir": str(screen_output_dir),
            "resolution": [800, 480],
            "orientation": "horizontal",
            "timezone": "Europe/Paris",
            "plugin_cycle_interval_seconds": 0.05,
            "playlist_config": {
                "playlists": [
                    {
                        "name": "Default",
                        "start_time": "00:00",
                        "end_time": "24:00",
                        "plugins": [
                            {
                                "plugin_id": "year_progress",
                                "name": "Year",
                                "plugin_settings": {},
                                "refresh": {"interval": 3600},
                            }
                        ],
                    }
                ],
                "active_playlist": "Default",
            },
        }
    )
    device_config = Config()
    load_plugins(device_config.get_plugins())
    refresh_task = RefreshTask(device_config, DisplayManager(device_config))
    refresh_task.start()
    yield refresh_task
    refresh_task.stop()


@pytest.fixture
def no_chromium_binary(monkeypatch):
    monkeypatch.setattr("utils.image_utils.shutil.which", lambda binary_name: None)


@pytest.fixture
def info_logs(caplog):
    caplog.set_level(logging.INFO)
    return caplog


def test_refresh_cycle_logs_the_failure_and_keeps_running_when_no_browser_is_installed(
    no_chromium_binary, info_logs, started_refresh_task, screen_output_dir, tmp_path
):
    failure_was_logged = wait_until(lambda: "Exception during refresh" in info_logs.text)
    next_cycle_ran = wait_until(lambda: info_logs.text.count("Running interval refresh check") >= 2)

    assert failure_was_logged
    assert next_cycle_ran
    assert started_refresh_task.thread.is_alive()
    assert "No Chromium-based browser found" in info_logs.text
    assert list(screen_output_dir.iterdir()) == []
    assert not (tmp_path / "current_image.png").exists()


def test_failed_refresh_cycles_leave_the_previous_screen_untouched(
    no_chromium_binary, info_logs, previous_screen, started_refresh_task, screen_output_dir
):
    several_cycles_failed = wait_until(lambda: info_logs.text.count("Exception during refresh") >= 3)

    assert several_cycles_failed
    assert {path: path.read_bytes() for path in previous_screen} == previous_screen
    assert {path.name for path in screen_output_dir.iterdir()} == {
        path.name for path in previous_screen if path.parent == screen_output_dir
    }


def test_refresh_cycle_writes_the_screen_when_a_browser_renders(
    fake_browser_screenshot, started_refresh_task, screen_output_dir
):
    screen_was_written = wait_until(lambda: (screen_output_dir / "latest.png").exists())

    assert screen_was_written
    assert "year" in fake_browser_screenshot.rendered_html.lower()
