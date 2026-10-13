from datetime import datetime
from pathlib import Path

import pytz
from conftest import (
    ICALENDAR_INSTANCE_NAME,
    TEST_TIMEZONE,
    device_json_with_icalendar_instance,
)

from plugins.plugin_registry import get_plugin_instance
from refresh_task import PlaylistRefresh

FORGED_CALENDAR_URL = "https://calendar.example.test/ical/FORGEDTOKEN456.ics"


def refresh_icalendar_instance_like_the_scheduler(device_config):
    """Runs one playlist refresh then persists the config, as RefreshTask does."""
    playlist = device_config.get_playlist_manager().get_playlist("Default")
    plugin_instance = playlist.find_plugin("icalendar", ICALENDAR_INSTANCE_NAME)
    plugin = get_plugin_instance({"id": "icalendar"})
    now = datetime.now(pytz.timezone(TEST_TIMEZONE))

    PlaylistRefresh(playlist, plugin_instance).execute(plugin, device_config, now)
    device_config.write_config()


def test_user_saves_calendar_settings_without_url_being_persisted(make_app, fake_chromium, calendar_network):
    app = make_app(device_json_with_icalendar_instance())
    device_config = app.config["DEVICE_CONFIG"]
    forged_form = {
        "plugin_id": "icalendar",
        "title": "Agenda",
        "viewMode": "list",
        "calendarUrl": FORGED_CALENDAR_URL,
    }

    response = app.test_client().put(f"/update_plugin_instance/{ICALENDAR_INSTANCE_NAME}", data=forged_form)
    refresh_icalendar_instance_like_the_scheduler(device_config)

    persisted_device_json = Path(device_config.config_file).read_text()
    assert response.status_code == 200
    assert "FORGEDTOKEN456" not in persisted_device_json
    assert "calendarUrl" not in persisted_device_json


def test_calendar_settings_form_offers_no_calendar_url_field(make_app):
    app = make_app()

    response = app.test_client().get("/plugin/icalendar")

    assert response.status_code == 200
    assert b'name="calendarUrl"' not in response.data
    assert b"CALENDAR_URL" in response.data


def test_fresh_clone_config_base_serves_the_home_page(make_app):
    app = make_app()

    response = app.test_client().get("/")

    assert response.status_code == 200
