import configparser
import json
import logging
import re
import threading
import time
import traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import icalendar
import pytest
import pytz
import requests

from config import Config
from plugins.calendar.calendar import Calendar
from plugins.plugin_registry import load_plugins

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TODAY_TOKEN = "@TODAY@"
DEVICE_TIMEZONE = "Europe/Paris"
SECRET_PATH_TOKEN = "secret-token-123"
SECRET_URL = f"https://example.invalid/{SECRET_PATH_TOKEN}"
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


def test_calendar_passes_the_french_locale_to_fullcalendar(serve_calendars, device_config, fake_browser_screenshot):
    """Checks that the chosen language reaches the FullCalendar options, nothing more.

    Known limit: the FullCalendar bundle ships without locale files, so the fr locale is not loaded.
    Month and day names come from Intl and the Chromium ICU data; interface texts stay in English.
    To confirm visually in L6; if English shows up, add fr.global.min.js in L4b.
    """
    serve_calendars({FIRST_CALENDAR_URL: "french_week"})
    settings = week_view_settings([FIRST_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert 'locale: "fr"' in html
    assert count_event_titles(html, "Dentiste") == 1


HOSTILE_SUMMARIES = [
    pytest.param("</script><img src=x onerror=alert(1)>", id="script-closing-tag"),
    pytest.param("<!--", id="html-comment-opening"),
    pytest.param('say "hello"', id="double-quotes"),
    pytest.param("l'été d'Éric", id="apostrophes"),
    pytest.param("back\\slash", id="backslashes"),
    pytest.param("first line\nsecond line", id="line-break"),
    pytest.param("&lt;b&gt; &amp; &#x27;", id="html-entities"),
    pytest.param("{{ 7*7 }}", id="template-expression"),
    pytest.param('{"valid": [1, 2]}', id="valid-json"),
]
CHARACTERS_THAT_BREAK_OUT_OF_A_SCRIPT_BLOCK = "<>&'"


def build_calendar_with_summary(summary):
    today_at_ten = datetime.now(ZoneInfo(DEVICE_TIMEZONE)).replace(hour=10, minute=0, second=0, microsecond=0)
    event = icalendar.Event()
    event.add("uid", "hostile@example.invalid")
    event.add("dtstamp", datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")))
    event.add("dtstart", today_at_ten)
    event.add("dtend", today_at_ten + timedelta(hours=1))
    event.add("summary", summary)
    calendar = icalendar.Calendar()
    calendar.add("version", "2.0")
    calendar.add("prodid", "-//example.invalid//calendar-fixture//FR")
    calendar.add_component(event)
    return calendar.to_ical().decode()


def extract_events_literal(html):
    return re.search(r"const events = (.*);$", html, re.MULTILINE).group(1)


@pytest.mark.parametrize("hostile_summary", HOSTILE_SUMMARIES)
def test_hostile_ics_summary_is_not_executed(
    hostile_summary, monkeypatch, device_config, fake_browser_screenshot
):
    monkeypatch.setattr(
        requests, "get", lambda url, *args, **kwargs: FakeCalendarResponse(build_calendar_with_summary(hostile_summary))
    )
    settings = week_view_settings([FIRST_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    events_literal = extract_events_literal(html)
    shown_titles = [event["title"] for event in json.loads(events_literal)]
    assert shown_titles == [hostile_summary]
    assert not any(character in events_literal for character in CHARACTERS_THAT_BREAK_OUT_OF_A_SCRIPT_BLOCK)


EVENTS_THAT_CANNOT_BE_PLACED = ["event_without_dtstart", "event_with_dtend_only", "rrule_without_dtstart"]


@pytest.mark.parametrize("fixture_name", EVENTS_THAT_CANNOT_BE_PLACED)
def test_event_without_dtstart_does_not_empty_the_calendar(
    fixture_name, serve_calendars, device_config, fake_browser_screenshot, caplog
):
    serve_calendars({FIRST_CALENDAR_URL: fixture_name})
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
    assert SECRET_PATH_TOKEN not in raised_text
    assert SECRET_PATH_TOKEN not in caplog.text


@pytest.mark.parametrize("make_exception", NETWORK_FAILURES)
def test_fetch_error_carries_no_chained_exception_that_could_hold_the_url(make_exception, failing_network):
    failing_network(make_exception)

    with pytest.raises(RuntimeError) as raised:
        Calendar({"id": "calendar"}).fetch_calendar(SECRET_URL)

    fetch_error = raised.value
    assert fetch_error.__context__ is None
    assert fetch_error.__cause__ is None
    assert SECRET_PATH_TOKEN not in str(fetch_error)
    assert SECRET_PATH_TOKEN not in "".join(traceback.format_exception(fetch_error))


def test_calendar_url_never_appears_in_ui_response_on_network_failure(failing_network, make_app, caplog):
    failing_network(lambda url: requests.exceptions.ConnectionError(f"HTTPSConnectionPool: max retries for {url}"))
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
    assert SECRET_PATH_TOKEN not in response.get_data(as_text=True)
    assert SECRET_PATH_TOKEN not in caplog.text


def test_webcal_url_is_fetched_over_https(serve_calendars, device_config, fake_browser_screenshot):
    webcal_url = "webcal://example.invalid/shared-calendar"
    https_url = "https://example.invalid/shared-calendar"
    requested_urls = serve_calendars({https_url: "french_week"})
    settings = week_view_settings([webcal_url])

    render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert requested_urls == [https_url]


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


def recurring_event_block(rrule, summary):
    return "\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{summary.lower()}@example.invalid",
            "DTSTAMP:20260101T000000Z",
            f"DTSTART:{todays_date_digits()}T000000Z",
            f"DTEND:{todays_date_digits()}T000001Z",
            f"RRULE:{rrule}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]
    )


def calendar_text_with_events(*event_blocks):
    header = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//example.invalid//calendar-fixture//FR"]
    return "\n".join([*header, *event_blocks, "END:VCALENDAR"])


@pytest.fixture
def serve_calendar_texts(monkeypatch):
    """Serves the given {url: ics text} over a patched requests.get."""

    def serve(texts_by_url):
        monkeypatch.setattr(requests, "get", lambda url, *args, **kwargs: FakeCalendarResponse(texts_by_url[url]))

    return serve


def test_calendar_with_unexpandable_rrule_does_not_empty_the_next_calendar(
    serve_calendar_texts, device_config, fake_browser_screenshot, caplog
):
    serve_calendar_texts(
        {
            FIRST_CALENDAR_URL: calendar_text_with_events(recurring_event_block("FREQ=DAILY;BYHOUR=25", "Hors-limites")),
            SECOND_CALENDAR_URL: read_calendar_fixture("second"),
        }
    )
    settings = week_view_settings([FIRST_CALENDAR_URL, SECOND_CALENDAR_URL])

    with caplog.at_level(logging.WARNING):
        html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Yoga") == 1
    assert count_event_titles(html, "Hors-limites") == 0
    assert "Skipping calendar" in caplog.text


DENSE_RRULES = [
    pytest.param("FREQ=SECONDLY", id="secondly"),
    pytest.param("FREQ=MINUTELY", id="minutely"),
    pytest.param("FREQ=DAILY;BYSECOND=0,30", id="several-seconds"),
]


@pytest.mark.parametrize("dense_rrule", DENSE_RRULES)
def test_event_recurring_by_the_second_or_minute_is_dropped_and_the_others_are_kept(
    dense_rrule, serve_calendar_texts, device_config, fake_browser_screenshot, caplog
):
    second_calendar_event = read_calendar_fixture("second").split("BEGIN:VEVENT", 1)[1].split("END:VEVENT", 1)[0]
    yoga_event_block = f"BEGIN:VEVENT{second_calendar_event}END:VEVENT"
    serve_calendar_texts(
        {SECRET_URL: calendar_text_with_events(recurring_event_block(dense_rrule, "Rafale"), yoga_event_block)}
    )
    settings = week_view_settings([SECRET_URL])

    with caplog.at_level(logging.WARNING):
        html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Rafale") == 0
    assert count_event_titles(html, "Yoga") == 1
    assert "skipping event recurring too densely" in caplog.text
    assert SECRET_PATH_TOKEN not in caplog.text


def test_dense_unbounded_recurrence_is_capped_quickly_and_the_warning_hides_the_url(
    serve_calendars, device_config, fake_browser_screenshot, caplog
):
    event_cap = 2000
    time_budget_in_seconds = 5
    serve_calendars({SECRET_URL: "unbounded_dense"})
    settings = week_view_settings([SECRET_URL])

    started_at = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        html = render_calendar_html(settings, device_config, fake_browser_screenshot)
    elapsed_in_seconds = time.perf_counter() - started_at

    assert count_event_titles(html, "Rafale") == event_cap
    assert elapsed_in_seconds < time_budget_in_seconds
    assert f"truncated to {event_cap} events" in caplog.text
    assert SECRET_PATH_TOKEN not in caplog.text


def test_recurring_event_appears_on_each_day_of_the_week(serve_calendars, device_config, fake_browser_screenshot):
    serve_calendars({FIRST_CALENDAR_URL: "recurring"})
    settings = week_view_settings([FIRST_CALENDAR_URL])

    html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Standup") == 3


SERVED_FEED_PATH = f"/{SECRET_PATH_TOKEN}/feed"
ABSENT_FEED_PATH = f"/{SECRET_PATH_TOKEN}/absent"
LOGGING_CONF = Path(__file__).parent.parent / "src" / "config" / "logging.conf"


@pytest.fixture
def local_calendar_url(outbound_network_guard):
    """Serves the french_week fixture on a real 127.0.0.1 socket and returns a function building urls on it."""

    class FeedHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            is_served_feed = self.path == SERVED_FEED_PATH
            body = read_calendar_fixture("french_week").encode() if is_served_feed else b"not found"
            self.send_response(200 if is_served_feed else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return None

    outbound_network_guard.allow("127.0.0.1")
    server = HTTPServer(("127.0.0.1", 0), FeedHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield lambda path: f"http://127.0.0.1:{server.server_port}{path}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("path", [SERVED_FEED_PATH, ABSENT_FEED_PATH], ids=["served", "not-found"])
def test_calendar_plugin_logs_never_contain_the_url_over_a_real_connection_at_debug(
    path, local_calendar_url, device_config, fake_browser_screenshot, caplog
):
    """Checks every record the plugin emits at DEBUG, over a real socket, for a path carrying a secret token.

    Known limit: urllib3 logs the request line, path included, at DEBUG. Those records are not the plugin's,
    so they are left out here; production is protected by the INFO level of the root logger, checked below.
    """
    settings = week_view_settings([local_calendar_url(path)])

    with caplog.at_level(logging.DEBUG):
        try:
            render_calendar_html(settings, device_config, fake_browser_screenshot)
        except RuntimeError as failure:
            assert SECRET_PATH_TOKEN not in str(failure)

    plugin_records = [record for record in caplog.records if not record.name.startswith("urllib3")]
    assert plugin_records
    assert not any(SECRET_PATH_TOKEN in record.getMessage() for record in plugin_records)


def test_production_logging_stays_at_info_so_urllib3_never_logs_the_path(
    local_calendar_url, device_config, fake_browser_screenshot, caplog
):
    logging_conf = configparser.ConfigParser()
    logging_conf.read(LOGGING_CONF)
    assert logging_conf["logger_root"]["level"] == "INFO"
    assert logging_conf["handler_consoleHandler"]["level"] == "INFO"
    settings = week_view_settings([local_calendar_url(SERVED_FEED_PATH)])

    with caplog.at_level(logging.INFO):
        render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert SECRET_PATH_TOKEN not in caplog.text


def plain_event_block(summary, start_utc):
    return "\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{summary.lower()}@example.invalid",
            "DTSTAMP:20260101T000000Z",
            f"DTSTART:{start_utc}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]
    )


def test_range_end_is_exclusive(serve_calendar_texts):
    paris = pytz.timezone(DEVICE_TIMEZONE)
    range_start = datetime(2030, 1, 1)
    range_end = datetime(2030, 1, 1, 11, 0)
    starts_exactly_at_range_end = plain_event_block("Pile", "20300101T100000Z")
    starts_just_before_range_end = plain_event_block("Juste-avant", "20300101T095900Z")
    serve_calendar_texts(
        {FIRST_CALENDAR_URL: calendar_text_with_events(starts_exactly_at_range_end, starts_just_before_range_end)}
    )

    events = Calendar({"id": "calendar"}).fetch_ics_events(
        [FIRST_CALENDAR_URL], ["#336699"], paris, range_start, range_end
    )

    assert [event["title"] for event in events] == ["Juste-avant"]


COLORS_WITH_AN_ALPHA_CHANNEL = [
    pytest.param("#1234", id="short-rgba"),
    pytest.param("#11223344", id="long-rgba"),
]


@pytest.mark.parametrize("color", COLORS_WITH_AN_ALPHA_CHANNEL)
def test_calendar_color_with_an_alpha_channel_is_rendered_as_is(
    color, serve_calendars, device_config, fake_browser_screenshot, caplog
):
    serve_calendars({FIRST_CALENDAR_URL: "french_week"})
    settings = week_view_settings([FIRST_CALENDAR_URL], **{"calendarColors[]": [color]})

    with caplog.at_level(logging.WARNING):
        html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Dentiste") == 1
    assert f'"{color}"' in html
    assert "using the default color" not in caplog.text


DEFAULT_COLOR = "#007BFF"
COLOR_SETTINGS_WITHOUT_A_COLOR_FOR_EACH_CALENDAR = [
    pytest.param(None, id="colors-absent"),
    pytest.param([], id="colors-empty"),
    pytest.param(["#336699"], id="colors-shorter-than-urls"),
    pytest.param(["not-a-color", "#996633"], id="color-invalid"),
]


@pytest.mark.parametrize("colors", COLOR_SETTINGS_WITHOUT_A_COLOR_FOR_EACH_CALENDAR)
def test_calendar_without_a_usable_color_is_shown_with_the_default_color(
    colors, serve_calendars, device_config, fake_browser_screenshot, caplog
):
    serve_calendars({FIRST_CALENDAR_URL: "french_week", SECOND_CALENDAR_URL: "second"})
    settings = week_view_settings([FIRST_CALENDAR_URL, SECOND_CALENDAR_URL], **{"calendarColors[]": colors})

    with caplog.at_level(logging.WARNING):
        html = render_calendar_html(settings, device_config, fake_browser_screenshot)

    assert count_event_titles(html, "Dentiste") == 1
    assert count_event_titles(html, "Yoga") == 1
    assert f'"{DEFAULT_COLOR}"' in html
    assert "using the default color" in caplog.text
