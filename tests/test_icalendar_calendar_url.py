import hashlib
import logging
from urllib.error import HTTPError, URLError

import pytest
from conftest import FIXTURES_DIR

from plugins.icalendar.icalendar import ICalendar

CALENDAR_URL = "https://calendar.example.test/ical/SECRETTOKEN123.ics"
WEBCAL_URL = "webcal://calendar.example.test/ical/SECRETTOKEN123.ics"
CALENDAR_URL_NOT_CONFIGURED_LOG = "calendar URL not configured"
DENTIST_EVENT_TITLE = "Dentiste"
CALENDAR_HEADER_MARKER = '<div class="header">'
WEEK_VIEW_SETTINGS = {"title": "Calendar", "viewMode": "week"}

LANDSCAPE_SIZE = (800, 480)
PORTRAIT_SIZE = (480, 800)

NETWORK_FAILURES_THAT_ECHO_THE_URL = {
    "url_error": lambda url: URLError(f"cannot reach {url}"),
    "value_error": lambda url: ValueError(f"unknown url type: '{url}'"),
    "http_error": lambda url: HTTPError(url, 404, f"Not Found for {url}", {}, None),
}


def render_calendar(device_config, settings=None):
    plugin = ICalendar({"id": "icalendar"})
    return plugin.generate_image(settings or {"title": "Calendar"}, device_config)


def test_calendar_shows_dentiste_event_in_landscape(
    make_device_config, calendar_network, fake_chromium, frozen_clock, dentiste_ics_bytes
):
    calendar_network.serve_ics(dentiste_ics_bytes)
    device_config = make_device_config(environment={"CALENDAR_URL": CALENDAR_URL})

    image = render_calendar(device_config, WEEK_VIEW_SETTINGS)

    assert image.size == LANDSCAPE_SIZE
    assert image.mode == "RGB"
    assert DENTIST_EVENT_TITLE in fake_chromium.rendered_html


def test_calendar_shows_dentiste_event_in_portrait(
    make_device_config, calendar_network, fake_chromium, frozen_clock, dentiste_ics_bytes
):
    calendar_network.serve_ics(dentiste_ics_bytes)
    device_config = make_device_config(
        orientation="vertical", environment={"CALENDAR_URL": CALENDAR_URL}
    )

    image = render_calendar(device_config, WEEK_VIEW_SETTINGS)

    assert image.size == PORTRAIT_SIZE
    assert image.mode == "RGB"
    assert DENTIST_EVENT_TITLE in fake_chromium.rendered_html


def test_calendar_fetches_webcal_url_over_https(
    make_device_config, calendar_network, fake_chromium, frozen_clock, dentiste_ics_bytes
):
    calendar_network.serve_ics(dentiste_ics_bytes)
    device_config = make_device_config(environment={"CALENDAR_URL": WEBCAL_URL})

    render_calendar(device_config)

    assert calendar_network.requested_urls == [CALENDAR_URL]


def test_calendar_without_configured_url_renders_empty_calendar(
    make_device_config, calendar_network, fake_chromium, frozen_clock, caplog
):
    caplog.set_level(logging.DEBUG)
    device_config = make_device_config(environment={})

    image = render_calendar(device_config)

    assert image.size == LANDSCAPE_SIZE
    assert image.mode == "RGB"
    assert CALENDAR_HEADER_MARKER in fake_chromium.rendered_html
    assert DENTIST_EVENT_TITLE not in fake_chromium.rendered_html
    assert calendar_network.requested_urls == []
    assert CALENDAR_URL_NOT_CONFIGURED_LOG in caplog.text


def test_calendar_ignores_a_calendar_url_left_in_plugin_settings(
    make_device_config, calendar_network, fake_chromium, frozen_clock, caplog
):
    caplog.set_level(logging.DEBUG)
    device_config = make_device_config(environment={})
    legacy_settings = {"title": "Calendar", "calendarUrl": CALENDAR_URL}

    render_calendar(device_config, settings=legacy_settings)

    assert calendar_network.requested_urls == []
    assert "calendarUrl" not in legacy_settings


@pytest.mark.parametrize(
    "failure",
    NETWORK_FAILURES_THAT_ECHO_THE_URL.values(),
    ids=NETWORK_FAILURES_THAT_ECHO_THE_URL.keys(),
)
def test_calendar_url_never_appears_in_logs_on_network_failure(
    make_device_config, calendar_network, fake_chromium, frozen_clock, caplog, failure
):
    caplog.set_level(logging.DEBUG)
    calendar_network.fail_with(failure)
    device_config = make_device_config(environment={"CALENDAR_URL": CALENDAR_URL})

    image = render_calendar(device_config)

    failure_type_name = type(failure(CALENDAR_URL)).__name__
    assert calendar_network.requested_urls == [CALENDAR_URL]
    assert image.size == LANDSCAPE_SIZE
    assert f"Error fetching calendar: {failure_type_name}" in caplog.text
    assert "SECRETTOKEN123" not in caplog.text
    assert "calendar.example.test" not in caplog.text


def test_calendar_html_matches_reference_for_dentiste_in_landscape(
    make_device_config, calendar_network, fake_chromium, frozen_clock, dentiste_ics_bytes
):
    calendar_network.serve_ics(dentiste_ics_bytes)
    device_config = make_device_config(environment={"CALENDAR_URL": CALENDAR_URL})
    reference_hash = (FIXTURES_DIR / "icalendar_dentiste_800x480.html.sha256").read_text().strip()

    image = render_calendar(device_config, WEEK_VIEW_SETTINGS)

    rendered_hash = hashlib.sha256(fake_chromium.rendered_html.encode("utf-8")).hexdigest()
    assert image.size == LANDSCAPE_SIZE
    assert rendered_hash == reference_hash
