import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config import Config
from plugins.plugin_registry import get_plugin_instance, load_plugins

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
ENGLISH_REFERENCE_HASHES = FIXTURES_DIR / "weather_english_reference_hashes.json"

DEVICE_TIMEZONE = "Europe/Paris"
FROZEN_NOW = datetime(2026, 10, 13, 9, 30, tzinfo=ZoneInfo(DEVICE_TIMEZONE))

OPEN_METEO = "OpenMeteo"
OPEN_WEATHER_MAP = "OpenWeatherMap"
BOTH_PROVIDERS = [OPEN_METEO, OPEN_WEATHER_MAP]

FRENCH_DATA_POINT_LABELS_ON_TILES = [
    ("Sunrise", "Lever"), ("Sunset", "Coucher"), ("Air Quality", "Qualité air"), ("UV Index", "UV"),
]

FIXTURE_BY_URL_PREFIX = {
    "https://api.open-meteo.com/": "open_meteo_forecast.json",
    "https://air-quality-api.open-meteo.com/": "open_meteo_air_quality.json",
    "https://api.openweathermap.org/data/3.0/onecall": "open_weather_map_onecall.json",
    "http://api.openweathermap.org/data/2.5/air_pollution": "open_weather_map_air_pollution.json",
}


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.astimezone(tz) if tz else FROZEN_NOW.replace(tzinfo=None)


class FakeHttpResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def serve_weather_fixtures(url, *args, **kwargs):
    matching_fixtures = [name for prefix, name in FIXTURE_BY_URL_PREFIX.items() if url.startswith(prefix)]
    assert matching_fixtures, "the weather plugin called an unexpected host"
    return FakeHttpResponse(json.loads((FIXTURES_DIR / matching_fixtures[0]).read_text()))


@pytest.fixture
def paris_system_clock(monkeypatch):
    """Open-Meteo times carry no offset: the plugin reads them in the system timezone."""
    with monkeypatch.context() as clock_patch:
        clock_patch.setenv("TZ", DEVICE_TIMEZONE)
        time.tzset()
        yield
    time.tzset()


@pytest.fixture
def frozen_weather_world(monkeypatch, paris_system_clock):
    """Freezes the plugin clock and serves the recorded provider responses instead of the network."""
    monkeypatch.setattr("plugins.weather.weather.datetime", FrozenDatetime)
    monkeypatch.setattr("plugins.weather.weather.requests.get", serve_weather_fixtures)
    monkeypatch.setenv("OPEN_WEATHER_MAP_SECRET", "test-key-not-a-secret")


@pytest.fixture
def weather_plugin(write_device_json, frozen_weather_world):
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
    device_config = Config()
    load_plugins(device_config.get_plugins())
    return get_plugin_instance({"id": "weather"}), device_config


@pytest.fixture
def render_weather_html(weather_plugin, fake_browser_screenshot):
    plugin, device_config = weather_plugin

    def render(provider, **extra_settings):
        settings = {
            "latitude": "48.85",
            "longitude": "2.35",
            "units": "metric",
            "weatherProvider": provider,
            "titleSelection": "custom",
            "customTitle": "Paris",
            "displayRefreshTime": "true",
            "displayMetrics": "true",
            "displayGraph": "true",
            "displayForecast": "true",
            "forecastDays": "5",
            "moonPhase": "true",
            **extra_settings,
        }
        plugin.generate_image(settings, device_config)
        return fake_browser_screenshot.rendered_html

    return render


def fingerprint_of(html):
    html_without_machine_paths = html.replace(str(SRC_DIR), "<src>")
    assert str(SRC_DIR.parent) not in html_without_machine_paths
    return hashlib.sha256(html_without_machine_paths.encode()).hexdigest()


@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
def test_weather_plugin_renders_french_labels(provider, render_weather_html):
    html = render_weather_html(provider, language="fr")

    for french_label in ["Humidité", "Pression", "Vent", "Ressenti", "Dernière mise à jour", "Visibilité"]:
        assert french_label in html
    for english_label in ["Humidity", "Pressure", "Feels Like", "Last refresh", "UV Index", "Visibility"]:
        assert english_label not in html
    for english_label, french_label in FRENCH_DATA_POINT_LABELS_ON_TILES:
        assert f">{french_label}<" in html
        assert f'alt="{french_label}"' in html
        assert f">{english_label}<" not in html
        assert f'alt="{english_label}"' not in html


@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
def test_weather_plugin_renders_french_weekday(provider, render_weather_html):
    html = render_weather_html(provider, language="fr")

    assert "mardi 13 octobre" in html
    assert "Tuesday" not in html
    assert '<div class="forecast-day-name">mer.</div>' in html


@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
def test_weather_plugin_translates_the_air_quality_scale_to_french(provider, render_weather_html):
    html = render_weather_html(provider, language="fr")

    assert "Passable" in html
    assert "Fair" not in html


@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
def test_wind_arrow_still_shown_in_french(provider, render_weather_html):
    html = render_weather_html(provider, language="fr")

    assert '<span class="wind-arrow-symbol">→</span>' in html


@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
def test_french_hourly_graph_keeps_the_upstream_hh00_labels(provider, render_weather_html):
    html = render_weather_html(provider, language="fr")

    assert '"09:00", "10:00", "11:00"' in html


@pytest.mark.parametrize("language_setting", [{}, {"language": "en"}], ids=["unset", "en"])
@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
def test_weather_defaults_to_english_like_upstream(provider, language_setting, render_weather_html):
    reference_hashes = json.loads(ENGLISH_REFERENCE_HASHES.read_text())

    html = render_weather_html(provider, **language_setting)

    assert fingerprint_of(html) == reference_hashes[provider]


@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
def test_weather_falls_back_to_english_for_an_unsupported_language(provider, render_weather_html):
    html = render_weather_html(provider, language="de")

    assert "Humidity" in html
    assert "Luftfeuchtigkeit" not in html


MALFORMED_LANGUAGE_SETTINGS = [
    pytest.param(None, id="none"),
    pytest.param([], id="empty-list"),
    pytest.param({}, id="empty-object"),
    pytest.param(42, id="integer"),
    pytest.param("klingon", id="unknown-string"),
    pytest.param("x" * 10_000, id="10000-characters"),
    pytest.param("<script>alert(1)</script><b>fr</b>", id="html-and-script"),
]


@pytest.mark.parametrize("provider", BOTH_PROVIDERS)
@pytest.mark.parametrize("malformed_language", MALFORMED_LANGUAGE_SETTINGS)
def test_weather_renders_english_for_a_malformed_language_setting(provider, malformed_language, render_weather_html):
    reference_hashes = json.loads(ENGLISH_REFERENCE_HASHES.read_text())

    html = render_weather_html(provider, language=malformed_language)

    assert fingerprint_of(html) == reference_hashes[provider]


def test_weather_settings_page_offers_the_language_choice(make_app):
    app = make_app()
    load_plugins(app.config["DEVICE_CONFIG"].get_plugins())

    page = app.test_client().get("/plugin/weather").get_data(as_text=True)

    assert 'name="language"' in page
    assert '<option value="en"' in page
    assert '<option value="fr"' in page
