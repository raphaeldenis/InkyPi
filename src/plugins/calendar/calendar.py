import os
from utils.app_utils import resolve_path, get_font
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.calendar.constants import DEFAULT_CALENDAR_COLOR, LOCALE_MAP, FONT_SIZES
from PIL import Image, ImageColor, ImageDraw, ImageFont
import icalendar
import recurring_ical_events
from io import BytesIO
import logging
import requests
from datetime import datetime, timedelta
from itertools import islice, takewhile
import pytz

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_CALENDAR = 2000
TOO_DENSE_FREQUENCIES = {"SECONDLY", "MINUTELY"}

class Calendar(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        template_params['locale_map'] = LOCALE_MAP
        return template_params

    def generate_image(self, settings, device_config):
        calendar_urls = settings.get('calendarURLs[]')
        calendar_colors = settings.get('calendarColors[]')
        view = settings.get("viewMode")

        if not view:
            raise RuntimeError("View is required")
        elif view not in ["timeGridDay", "timeGridWeek", "dayGrid", "dayGridMonth", "listMonth"]:
            raise RuntimeError("Invalid view")

        if not calendar_urls:
            raise RuntimeError("At least one calendar URL is required")
        for url in calendar_urls:
            if not url.strip():
                raise RuntimeError("Invalid calendar URL")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        
        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        tz = pytz.timezone(timezone)

        current_dt = datetime.now(tz)
        start, end = self.get_view_range(view, current_dt, settings)
        logger.debug(f"Fetching events for {start} --> [{current_dt}] --> {end}")
        events = self.fetch_ics_events(calendar_urls, calendar_colors, tz, start, end)
        if not events:
            logger.warning("No events found for ics url")

        if view == 'timeGridWeek' and settings.get("displayPreviousDays") != "true":
            view = 'timeGrid'

        template_params = {
            "view": view,
            "events": events,
            "current_dt": current_dt.replace(minute=0, second=0, microsecond=0).isoformat(),
            "timezone": timezone,
            "plugin_settings": settings,
            "time_format": time_format,
            "font_scale": FONT_SIZES.get(settings.get("fontSize", "normal"))
        }

        image = self.render_image(dimensions, "calendar.html", "calendar.css", template_params)

        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image
    
    def fetch_ics_events(self, calendar_urls, colors, tz, start_range, end_range):
        parsed_events = []

        last_fetch_error = None
        loaded_calendar_count = 0

        for calendar_index, calendar_url in enumerate(calendar_urls):
            color = self.color_or_default(colors, calendar_index)
            try:
                events = self.load_calendar_events(calendar_url, tz, start_range, end_range)
            except RuntimeError as load_error:
                logger.warning(f"Skipping calendar: {load_error}")
                last_fetch_error = load_error
                continue
            loaded_calendar_count += 1
            contrast_color = self.get_contrast_color(color)

            for event in events:
                start, end, all_day = self.parse_data_points(event, tz)
                parsed_event = {
                    "title": str(event.get("summary")),
                    "start": start,
                    "backgroundColor": color,
                    "textColor": contrast_color,
                    "allDay": all_day
                }
                if end:
                    parsed_event['end'] = end

                parsed_events.append(parsed_event)

        no_calendar_could_be_loaded = last_fetch_error is not None and loaded_calendar_count == 0
        if no_calendar_could_be_loaded:
            raise last_fetch_error

        return parsed_events
    
    def load_calendar_events(self, calendar_url, tz, start_range, end_range):
        """Returns the events of one calendar in the range, or raises a RuntimeError that never carries the url."""
        cal = self.fetch_calendar(calendar_url)
        failure_name = None
        try:
            self.drop_events_without_start(cal)
            self.drop_events_recurring_too_densely(cal)
            return self.capped_events_in_range(cal, tz, start_range, end_range)
        except (ValueError, TypeError) as e:
            failure_name = type(e).__name__
        raise RuntimeError(f"Failed to read iCalendar events: {failure_name}")

    def color_or_default(self, colors, calendar_index):
        """Returns the calendar's own color, or the default one when it is missing or not a color."""
        has_color_at_index = bool(colors) and calendar_index < len(colors)
        color = colors[calendar_index] if has_color_at_index else None
        try:
            ImageColor.getrgb(color)
            return color
        except (ValueError, TypeError):
            logger.warning(f"Calendar {calendar_index + 1} has no valid color, using the default color")
            return DEFAULT_CALENDAR_COLOR

    def capped_events_in_range(self, cal, tz, start_range, end_range):
        """Returns at most MAX_EVENTS_PER_CALENDAR events of the range, without expanding the whole range."""
        end_of_range = self.as_naive_local_datetime(end_range, tz)
        events_from_range_start = recurring_ical_events.of(cal).after(start_range)
        events_until_range_end = takewhile(
            lambda event: self.as_naive_local_datetime(event.decoded("dtstart"), tz) < end_of_range,
            events_from_range_start,
        )
        events_and_one_more = list(islice(events_until_range_end, MAX_EVENTS_PER_CALENDAR + 1))

        is_over_the_cap = len(events_and_one_more) > MAX_EVENTS_PER_CALENDAR
        if is_over_the_cap:
            logger.warning(f"Calendar events truncated to {MAX_EVENTS_PER_CALENDAR} events")
        return events_and_one_more[:MAX_EVENTS_PER_CALENDAR]

    def as_naive_local_datetime(self, moment, tz):
        """Returns a date or datetime as a naive datetime on the device clock, comparable with any other."""
        if not isinstance(moment, datetime):
            return datetime(moment.year, moment.month, moment.day)
        is_naive = moment.tzinfo is None
        return moment if is_naive else moment.astimezone(tz).replace(tzinfo=None)

    def drop_events_without_start(self, cal):
        """Removes the events that have no DTSTART, which the recurrence expansion cannot handle."""
        events_without_start = [
            component for component in cal.subcomponents
            if component.name == "VEVENT" and "dtstart" not in component
        ]
        for event in events_without_start:
            logger.warning("skipping event without start")
            cal.subcomponents.remove(event)

    def drop_events_recurring_too_densely(self, cal):
        """Removes the events repeating every second or minute, which would flood the expansion."""
        too_dense_events = [
            component for component in cal.subcomponents
            if component.name == "VEVENT" and self.has_too_dense_rrule(component)
        ]
        for event in too_dense_events:
            logger.warning("skipping event recurring too densely")
            cal.subcomponents.remove(event)

    def has_too_dense_rrule(self, event):
        rrules = event.get("rrule", [])
        rrules = rrules if isinstance(rrules, list) else [rrules]
        return any(self.is_too_dense_rrule(rrule) for rrule in rrules)

    def is_too_dense_rrule(self, rrule):
        repeats_every_second_or_minute = bool(set(rrule.get("FREQ", [])) & TOO_DENSE_FREQUENCIES)
        fires_several_times_a_minute = len(rrule.get("BYSECOND", [])) > 1
        return repeats_every_second_or_minute or fires_several_times_a_minute

    def get_view_range(self, view, current_dt, settings):
        start = datetime(current_dt.year, current_dt.month, current_dt.day)
        if view == "timeGridDay":
            end = start + timedelta(days=1)
        elif view == "timeGridWeek":
            if settings.get("displayPreviousDays") == "true":
                week_start_day = int(settings.get("weekStartDay", 1))
                python_week_start = (week_start_day - 1) % 7
                offset = (current_dt.weekday() - python_week_start) % 7
                start = current_dt - timedelta(days=offset)
                start = datetime(start.year, start.month, start.day)
            end = start + timedelta(days=7)
        elif view == "dayGrid":
            start = current_dt - timedelta(weeks=1)
            end = current_dt + timedelta(weeks=int(settings.get("displayWeeks") or 4))
        elif view == "dayGridMonth":
            start = datetime(current_dt.year, current_dt.month, 1) - timedelta(weeks=1)
            end = datetime(current_dt.year, current_dt.month, 1) + timedelta(weeks=6)
        elif view == "listMonth":
            end = start + timedelta(weeks=5)
        return start, end
        
    def parse_data_points(self, event, tz):
        all_day = False
        dtstart = event.decoded("dtstart")
        if isinstance(dtstart, datetime):
            start = dtstart.astimezone(tz).isoformat()
        else:
            start = dtstart.isoformat()
            all_day = True

        end = None
        if "dtend" in event:
            dtend = event.decoded("dtend")
            if isinstance(dtend, datetime):
                end = dtend.astimezone(tz).isoformat()
            else:
                end = dtend.isoformat()
        elif "duration" in event:
            duration = event.decoded("duration")
            end = (dtstart + duration).isoformat()
        return start, end, all_day

    def fetch_calendar(self, calendar_url):
        # workaround for webcal urls
        if calendar_url.startswith("webcal://"):
            calendar_url = calendar_url.replace("webcal://", "https://")
        failure_name = None
        try:
            response = requests.get(calendar_url, timeout=30)
            response.raise_for_status()
            return icalendar.Calendar.from_ical(response.text)
        except Exception as e:
            failure_name = type(e).__name__
        raise RuntimeError(f"Failed to fetch iCalendar url: {failure_name}")

    def get_contrast_color(self, color):
        """
        Returns '#000000' (black) or '#ffffff' (white) depending on the contrast
        against the given color.
        """
        r, g, b = ImageColor.getrgb(color)[:3]
        # YIQ formula to estimate brightness
        yiq = (r * 299 + g * 587 + b * 114) / 1000

        return '#000000' if yiq >= 150 else '#ffffff'
