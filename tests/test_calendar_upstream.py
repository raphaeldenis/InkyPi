import logging
import re
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from config import Config
from plugins.calendar.calendar import Calendar
from plugins.plugin_registry import load_plugins

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TODAY_TOKEN = "@TODAY@"
DEVICE_TIMEZONE = "Europe/Paris"
SECRET_URL = "https://example.invalid/secret-token-123"
SECRET_VALUE = "example.invalid/secret-token-123"
FIRST_CALENDAR_URL = "https://example.invalid/first-calendar"
SECOND_CALENDAR_URL = "https://example.invalid/second-calendar"


def todays_date_digits():
    return datetime.now(ZoneInfo(DEVICE_TIMEZONE)).strftime("%Y%m%d")


def read_calendar_fixture(name):
    fixture_path = next(FIXTURES_DIR.glob(f"calendar_{name}.*"))
    return fixture_path.read_text().replace(TODAY_TOKEN, todays_date_digits())


class FakeCalendarResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


@pytest.fixture
def serve_calendars(monkeypatch):
    """Serves the given {url: fixture name} over a patched requests.get and records the requested urls."""
    requested_urls = []

    def serve(calendars_by_url):
        def fake_get(url, *args, **kwargs):
            requested_urls.append(url)
            return FakeCalendarResponse(read_calendar_fixture(calendars_by_url[url]))

        monkeypatch.setattr(requests, "get", fake_get)
        return requested_urls

    return serve


@pytest.fixture
def device_config(write_device_json):
    write_device_json(
        {
            "name": "InkyPi test",
            "display_type": "mock",
            "resolution": [800, 480],
            "orientation": "horizontal",
            "timezone": DEVICE_TIMEZONE,
            "time_format": "24h",
        }
    )
    return Config()


def week_view_settings(calendar_urls, **overrides):
    return {
        "viewMode": "timeGridWeek",
        "language": "fr",
        "calendarURLs[]": calendar_urls,
        "calendarColors[]": ["#336699", "#996633"][: len(calendar_urls)],
        **overrides,
    }


def render_calendar_html(settings, device_config, fake_browser_screenshot):
    Calendar({"id": "calendar"}).generate_image(settings, device_config)
    return fake_browser_screenshot.rendered_html


def count_event_titles(html, title):
    return len(re.findall(rf'"title":\s*"{re.escape(title)}"', html))


def test_calendar_shows_french_week_view(serve_calendars, device_config, fake_browser_screenshot):
    serve_calendars({FIRST_CALENDAR_URL: "french_week"})
    """Checks that the chosen language reaches FullCalendar.

    Known limit: the FullCalendar bundle ships without locale files, so the fr locale is not loaded.
    Month and day names come from Intl and the Chromium ICU data; interface texts stay in English.
    To confirm visually in L6; if English shows up, add fr.global.min.js in L4b.
    """
    settings = week_view_settings([FIRST_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert 'locale: "fr"' in html
    assert count_event_titles(html, "Dentiste") == 1


def test_hostile_ics_summary_is_not_executed(serve_calendars, device_config, fake_browser_screenshot):
    serve_calendars({FIRST_CALENDAR_URL: "hostile_summary"})
    settings = week_view_settings([FIRST_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert "</script><img" not in html
    assert "\\u003c/script\\u003e\\u003cimg" in html


def test_event_without_dtstart_does_not_empty_the_calendar(
    serve_calendars, device_config, fake_browser_screenshot, caplog
):
    serve_calendars({FIRST_CALENDAR_URL: "event_without_dtstart"})
    settings = week_view_settings([FIRST_CALENDAR_URL])

    with caplog.at_level(logging.WARNING):
        html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Dentiste") == 1
    assert "skipping event without start" in caplog.text


NETWORK_FAILURES = [
    pytest.param(lambda url: requests.exceptions.ConnectionError(f"HTTPSConnectionPool: max retries for {url}"), id="connection"),
    pytest.param(lambda url: requests.exceptions.HTTPError(f"404 Client Error: Not Found for url: {url}"), id="http"),
    pytest.param(lambda url: requests.exceptions.InvalidURL(f"Invalid URL '{url}': No host supplied"), id="invalid-url"),
]


@pytest.fixture
def failing_network(monkeypatch):
    def fail_with(make_exception):
        def fake_get(url, *args, **kwargs):
            raise make_exception(url)

        monkeypatch.setattr(requests, "get", fake_get)

    return fail_with


@pytest.mark.parametrize("make_exception", NETWORK_FAILURES)
def test_calendar_url_never_appears_in_logs_on_network_failure(
    make_exception, failing_network, device_config, fake_browser_screenshot, caplog
):
    failing_network(make_exception)
    settings = week_view_settings([SECRET_URL])

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception) as raised:
            Calendar({"id": "calendar"}).generate_image(settings, device_config)

    raised_text = "".join(traceback.format_exception(raised.value))
    assert SECRET_VALUE not in str(raised.value)
    assert SECRET_VALUE not in raised_text
    assert SECRET_VALUE not in caplog.text


@pytest.mark.parametrize("make_exception", NETWORK_FAILURES)
def test_calendar_url_never_appears_in_ui_response_on_network_failure(
    make_exception, failing_network, make_app, caplog
):
    failing_network(make_exception)
    app = make_app({"resolution": [800, 480], "orientation": "horizontal", "timezone": DEVICE_TIMEZONE})
    load_plugins(app.config["DEVICE_CONFIG"].get_plugins())
    app.config["REFRESH_TASK"] = SimpleNamespace(running=False)
    app.config["DISPLAY_MANAGER"] = SimpleNamespace(display_image=lambda *args, **kwargs: None)
    form = {
        "plugin_id": "calendar",
        "viewMode": "timeGridWeek",
        "calendarURLs[]": [SECRET_URL],
        "calendarColors[]": ["#336699"],
    }

    with caplog.at_level(logging.DEBUG):
        response = app.test_client().post("/update_now", data=form)

    assert response.status_code == 500
    assert SECRET_VALUE not in response.get_data(as_text=True)
    assert SECRET_VALUE not in caplog.text


def test_webcal_url_is_fetched_over_https(serve_calendars, device_config, fake_browser_screenshot):
    webcal_url = "webcal://example.invalid/shared-calendar"
    https_url = "https://example.invalid/shared-calendar"
    requested_urls = serve_calendars({https_url: "french_week"})
    settings = week_view_settings([webcal_url])

    render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert requested_urls == [https_url]
    assert all(url.startswith("https://") for url in requested_urls)


def test_two_calendars_are_both_read_in_the_given_order(serve_calendars, device_config, fake_browser_screenshot):
    requested_urls = serve_calendars({FIRST_CALENDAR_URL: "french_week", SECOND_CALENDAR_URL: "second"})
    settings = week_view_settings([FIRST_CALENDAR_URL, SECOND_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert requested_urls == [FIRST_CALENDAR_URL, SECOND_CALENDAR_URL]
    assert html.index('"Dentiste"') < html.index('"Yoga"')
    assert '"#336699"' in html and '"#996633"' in html


def test_failing_calendar_does_not_empty_the_other_one(monkeypatch, device_config, fake_browser_screenshot):
    def fake_get(url, *args, **kwargs):
        if url == FIRST_CALENDAR_URL:
            raise requests.exceptions.ConnectionError("unreachable")
        return FakeCalendarResponse(read_calendar_fixture("second"))

    monkeypatch.setattr(requests, "get", fake_get)
    settings = week_view_settings([FIRST_CALENDAR_URL, SECOND_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Yoga") == 1


def test_recurring_event_appears_on_each_day_of_the_week(serve_calendars, device_config, fake_browser_screenshot):
    serve_calendars({FIRST_CALENDAR_URL: "recurring"})
    settings = week_view_settings([FIRST_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Standup") == 3
