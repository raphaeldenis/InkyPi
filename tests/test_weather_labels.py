from datetime import date

import pytest

from plugins.weather.labels import (
    AIR_QUALITY_SCALE_IN_FRENCH,
    LABELS,
    format_long_date,
    format_weekday_abbreviation,
    get_labels,
    translate_data_points,
)

DATA_POINT_LABEL_KEYS = [
    "Sunrise", "Sunset", "Wind", "Humidity", "Pressure", "UV Index", "Visibility", "Air Quality",
]
EXTRA_CHARACTERS_ALLOWED_OVER_ENGLISH_LABEL = 2
MAX_AIR_QUALITY_UNIT_LENGTH = 10

FRENCH_MONTHS_EXPECTED = [
    (1, "janvier"), (2, "février"), (3, "mars"), (4, "avril"), (5, "mai"), (6, "juin"),
    (7, "juillet"), (8, "août"), (9, "septembre"), (10, "octobre"), (11, "novembre"), (12, "décembre"),
]
FRENCH_WEEKDAYS_EXPECTED = [
    (date(2026, 10, 12), "lundi", "lun."),
    (date(2026, 10, 13), "mardi", "mar."),
    (date(2026, 10, 14), "mercredi", "mer."),
    (date(2026, 10, 15), "jeudi", "jeu."),
    (date(2026, 10, 16), "vendredi", "ven."),
    (date(2026, 10, 17), "samedi", "sam."),
    (date(2026, 10, 18), "dimanche", "dim."),
]
FRENCH_DATA_POINT_LABELS_EXPECTED = [
    ("Sunrise", "Lever"),
    ("Sunset", "Coucher"),
    ("Wind", "Vent"),
    ("Humidity", "Humidité"),
    ("Pressure", "Pression"),
    ("UV Index", "UV"),
    ("Visibility", "Visibilité"),
    ("Air Quality", "Qualité air"),
]
FRENCH_AIR_QUALITY_LEVELS_EXPECTED = [
    ("Good", "Bon"),
    ("Fair", "Passable"),
    ("Moderate", "Modéré"),
    ("Poor", "Mauvais"),
    ("Very Poor", "T. mauvais"),
    ("Ext Poor", "Extrême"),
]


@pytest.mark.parametrize("month_number, french_month", FRENCH_MONTHS_EXPECTED)
def test_format_long_date_writes_every_month_in_french(month_number, french_month):
    fifteenth_of_month = date(2026, month_number, 15)

    long_date = format_long_date(fifteenth_of_month, "fr")

    assert long_date.endswith(f"15 {french_month}")


@pytest.mark.parametrize("day, french_weekday, french_abbreviation", FRENCH_WEEKDAYS_EXPECTED)
def test_format_long_date_writes_every_weekday_in_french(day, french_weekday, french_abbreviation):
    long_date = format_long_date(day, "fr")

    assert long_date.startswith(f"{french_weekday} ")


@pytest.mark.parametrize("day, french_weekday, french_abbreviation", FRENCH_WEEKDAYS_EXPECTED)
def test_format_weekday_abbreviation_writes_every_abbreviation_in_french(day, french_weekday, french_abbreviation):
    assert format_weekday_abbreviation(day, "fr") == french_abbreviation


def test_format_long_date_writes_the_first_of_the_month_as_premier():
    first_of_october = date(2026, 10, 1)

    assert format_long_date(first_of_october, "fr") == "jeudi 1er octobre"


def test_format_long_date_writes_the_other_days_as_plain_numbers():
    assert format_long_date(date(2026, 10, 13), "fr") == "mardi 13 octobre"
    assert format_long_date(date(2026, 10, 2), "fr") == "vendredi 2 octobre"


@pytest.mark.parametrize("english_key, french_label", FRENCH_DATA_POINT_LABELS_EXPECTED)
def test_french_data_point_label_is_translated(english_key, french_label):
    assert get_labels("fr")[english_key] == french_label


@pytest.mark.parametrize("english_key", DATA_POINT_LABEL_KEYS)
def test_french_data_point_label_fits_the_tile_like_the_english_one(english_key):
    english_label = LABELS["en"][english_key]
    french_label = LABELS["fr"][english_key]

    longest_label_allowed = len(english_label) + EXTRA_CHARACTERS_ALLOWED_OVER_ENGLISH_LABEL

    assert len(french_label) <= longest_label_allowed


@pytest.mark.parametrize("english_level, french_level", FRENCH_AIR_QUALITY_LEVELS_EXPECTED)
def test_air_quality_level_is_translated_to_french(english_level, french_level):
    data_points = [{"label": "Air Quality", "unit": english_level}]

    translated = translate_data_points(data_points, "fr")

    assert translated[0]["unit"] == french_level


def test_air_quality_scale_covers_the_six_upstream_levels():
    assert list(AIR_QUALITY_SCALE_IN_FRENCH) == [english for english, _ in FRENCH_AIR_QUALITY_LEVELS_EXPECTED]


@pytest.mark.parametrize("english_level", list(AIR_QUALITY_SCALE_IN_FRENCH))
def test_french_air_quality_unit_fits_the_tile(english_level):
    french_level = AIR_QUALITY_SCALE_IN_FRENCH[english_level]

    assert len(french_level) <= MAX_AIR_QUALITY_UNIT_LENGTH


def test_english_air_quality_unit_is_left_untouched():
    data_points = [{"label": "Air Quality", "unit": "Very Poor"}]

    translated = translate_data_points(data_points, "en")

    assert translated[0]["unit"] == "Very Poor"
