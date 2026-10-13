DEFAULT_LANGUAGE = "en"
FRENCH = "fr"

LABELS = {
    "en": {
        "last_refresh": "Last refresh:",
        "feels_like": "Feels Like",
    },
    "fr": {
        "last_refresh": "Dernière mise à jour :",
        "feels_like": "Ressenti",
        "Sunrise": "Lever",
        "Sunset": "Coucher",
        "Wind": "Vent",
        "Humidity": "Humidité",
        "Pressure": "Pression",
        "UV Index": "UV",
        "Visibility": "Visibilité",
        "Air Quality": "Qualité air",
    },
}

AIR_QUALITY_SCALE_IN_FRENCH = {
    "Good": "Bon",
    "Fair": "Passable",
    "Moderate": "Modéré",
    "Poor": "Mauvais",
    "Very Poor": "T. mauvais",
    "Ext Poor": "Extrême",
}

FRENCH_WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FRENCH_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def resolve_language(requested_language):
    """Returns the requested language when it is supported, the default language otherwise."""
    return requested_language if requested_language in LABELS else DEFAULT_LANGUAGE


def format_long_date(moment, language):
    """Returns the weekday, month and day of the moment as written in the resolved language."""
    if language != FRENCH:
        return moment.strftime("%A, %B %d")
    weekday = FRENCH_WEEKDAYS[moment.weekday()]
    month = FRENCH_MONTHS[moment.month - 1]
    is_first_of_month = moment.day == 1
    day_of_month = "1er" if is_first_of_month else str(moment.day)
    return f"{weekday} {day_of_month} {month}"


def format_weekday_abbreviation(moment, language):
    """Returns the abbreviated weekday of the moment as written in the resolved language."""
    if language != FRENCH:
        return moment.strftime("%a")
    return FRENCH_WEEKDAYS[moment.weekday()][:3] + "."


def translate_data_points(data_points, language):
    """Adds the display label of the resolved language to each data point and translates the air quality scale.

    The "label" key stays the English identifier the template tests on (the wind arrow).
    """
    labels = LABELS[language]
    translates_air_quality_scale = language == FRENCH
    for data_point in data_points:
        data_point["display_label"] = labels.get(data_point["label"], data_point["label"])
        is_air_quality = data_point["label"] == "Air Quality"
        if translates_air_quality_scale and is_air_quality:
            data_point["unit"] = AIR_QUALITY_SCALE_IN_FRENCH.get(data_point["unit"], data_point["unit"])
    return data_points
