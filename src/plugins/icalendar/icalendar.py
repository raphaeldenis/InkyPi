import os
from datetime import datetime, timedelta, date as dt_date
import logging
from io import BytesIO
import pytz
import urllib.request
from icalendar import Calendar
import recurring_ical_events
from PIL import Image, ImageDraw
from utils.app_utils import get_font, resolve_path
from plugins.base_plugin.base_plugin import BasePlugin
import re

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "US/Eastern"
DEFAULT_DAYS_TO_SHOW = 7
DEFAULT_MAX_EVENTS = 10
DEFAULT_VIEW_MODE = "list"  # Options: "list", "week", "day"

# Color schemes
COLOR_SCHEMES = {
    "blue": {
        "primary": (65, 105, 225, 255),  # RoyalBlue
        "secondary": (240, 248, 255, 255),  # AliceBlue
        "text_dark": (20, 20, 20, 255),
        "text_medium": (80, 80, 80, 255),
        "text_light": (120, 120, 120, 255),
        "background": (255, 255, 255, 255),
        "grid_lines": (200, 200, 200, 255),
        "highlight": (30, 144, 255, 255),  # DodgerBlue
        "event_bg": (220, 240, 255, 255),
    },
    "dark": {
        "primary": (50, 50, 50, 255),
        "secondary": (80, 80, 80, 255),
        "text_dark": (240, 240, 240, 255),
        "text_medium": (200, 200, 200, 255),
        "text_light": (160, 160, 160, 255),
        "background": (30, 30, 30, 255),
        "grid_lines": (60, 60, 60, 255),
        "highlight": (100, 100, 100, 255),
        "event_bg": (45, 45, 45, 255),
    },
    "green": {
        "primary": (46, 139, 87, 255),  # SeaGreen
        "secondary": (240, 255, 240, 255),  # Honeydew
        "text_dark": (20, 20, 20, 255),
        "text_medium": (80, 80, 80, 255),
        "text_light": (120, 120, 120, 255),
        "background": (255, 255, 255, 255),
        "grid_lines": (200, 220, 200, 255),
        "highlight": (60, 179, 113, 255),  # MediumSeaGreen
        "event_bg": (220, 255, 220, 255),
    },
    "purple": {
        "primary": (106, 90, 205, 255),  # SlateBlue
        "secondary": (248, 240, 255, 255),
        "text_dark": (20, 20, 20, 255),
        "text_medium": (80, 80, 80, 255),
        "text_light": (120, 120, 120, 255),
        "background": (255, 255, 255, 255),
        "grid_lines": (220, 200, 220, 255),
        "highlight": (147, 112, 219, 255),  # MediumPurple
        "event_bg": (240, 230, 255, 255),
    },
    "inky": {  # For e-ink displays
        "primary": (0, 0, 0, 255),
        "secondary": (245, 245, 245, 255),
        "text_dark": (0, 0, 0, 255),
        "text_medium": (50, 50, 50, 255),
        "text_light": (100, 100, 100, 255),
        "background": (255, 255, 255, 255),
        "grid_lines": (200, 200, 200, 255),
        "highlight": (0, 0, 0, 255),
        "event_bg": (230, 230, 230, 255),
    }
}

class ICalendar(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        return template_params

    def generate_image(self, settings, device_config):
        # Get settings
        url = settings.get('calendarUrl', '')
        days_to_show = int(settings.get('daysToShow', DEFAULT_DAYS_TO_SHOW))
        max_events = int(settings.get('maxEvents', DEFAULT_MAX_EVENTS))
        title = settings.get('title', 'Calendar')
        view_mode = settings.get('viewMode', DEFAULT_VIEW_MODE)
        color_scheme = settings.get('colorScheme', 'blue')
        
        # Get display dimensions
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        
        # Get timezone setting
        timezone_name = device_config.get_config("timezone") or DEFAULT_TIMEZONE
        tz = pytz.timezone(timezone_name)
        
        # Get current time in the configured timezone
        now = datetime.now(tz)
        
        try:
            # Fix webcal URLs
            if url.startswith('webcal:'):
                url = url.replace('webcal:', 'https:', 1)
            
            # Fetch and parse the calendar events
            events = self.fetch_calendar_events(url, now, days_to_show, max_events, tz)
            
            # Select the appropriate view based on mode
            if view_mode == "week":
                return self.render_week_view(dimensions, title, events, now, color_scheme)
            elif view_mode == "day":
                return self.render_day_view(dimensions, title, events, now, color_scheme)
            else:  # Default to list view
                return self.render_list_view(dimensions, title, events, now, color_scheme)
                
        except Exception as e:
            logger.error(f"Failed to generate calendar image: {str(e)}")
            return self.render_error_image(dimensions, str(e))
    
    def fetch_calendar_events(self, url, now, days_to_show, max_events, tz):
        """Fetch and parse iCalendar events."""
        if not url:
            return []
        
        try:
            # Download the iCalendar file
            response = urllib.request.urlopen(url)
            ical_data = response.read()
            
            # Parse the iCalendar data
            cal = Calendar.from_ical(ical_data)
            
            # Define the date range
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=days_to_show)
            
            # Get events from the calendar, including recurring ones
            events = recurring_ical_events.of(cal).between(start_date, end_date)
            
            # Process events
            event_list = []
            for event in events:
                summary = str(event.get('summary', 'No Title'))
                location = str(event.get('location', ''))
                
                dtstart = event.get('dtstart').dt
                dtend = event.get('dtend').dt if event.get('dtend') else dtstart
                
                # Convert datetime to timezone-aware if it isn't already
                if isinstance(dtstart, datetime) and dtstart.tzinfo is None:
                    dtstart = tz.localize(dtstart)
                if isinstance(dtend, datetime) and dtend.tzinfo is None:
                    dtend = tz.localize(dtend)
                
                # Handle all-day events
                all_day = not isinstance(dtstart, datetime)
                
                event_list.append({
                    'summary': summary,
                    'location': location,
                    'start': dtstart,
                    'end': dtend,
                    'all_day': all_day
                })
            
            # Sort events by start time
            event_list.sort(key=lambda x: x['start'])
            
            # Limit the number of events
            return event_list[:max_events]
            
        except Exception as e:
            logger.error(f"Error fetching calendar: {str(e)}")
            return []
    
    def get_fonts(self, width):
        """Get the fonts needed for rendering based on screen width."""
        fonts = {}
        try:
            # Try with the semibold font
            fonts["title"] = get_font("Jost-SemiBold", int(width * 0.08))
            fonts["date"] = get_font("Jost-SemiBold", int(width * 0.05))
            fonts["header"] = get_font("Jost-SemiBold", int(width * 0.045))
            fonts["event"] = get_font("Jost", int(width * 0.04))
            fonts["time"] = get_font("Jost", int(width * 0.035))
            fonts["small"] = get_font("Jost", int(width * 0.03))
        except:
            # Fallback to regular font
            try:
                fonts["title"] = get_font("Jost", int(width * 0.08))
                fonts["date"] = get_font("Jost", int(width * 0.05))
                fonts["header"] = get_font("Jost", int(width * 0.045))
                fonts["event"] = get_font("Jost", int(width * 0.04))
                fonts["time"] = get_font("Jost", int(width * 0.035))
                fonts["small"] = get_font("Jost", int(width * 0.03))
            except:
                # Last resort fallback
                fonts["title"] = None
                fonts["date"] = None
                fonts["header"] = None
                fonts["event"] = None
                fonts["time"] = None
                fonts["small"] = None
        return fonts
    
    def render_list_view(self, dimensions, title, events, now, color_scheme="blue"):
        """Render the calendar events as a list."""
        width, height = dimensions
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["blue"])
        
        # Create the image
        image = Image.new("RGBA", dimensions, colors["background"])
        draw = ImageDraw.Draw(image)
        
        # Get fonts
        fonts = self.get_fonts(width)
        
        # Calculate dimensions
        padding = int(width * 0.05)
        title_height = int(height * 0.1)
        
        # Create header with gradient
        header_gradient = self._create_gradient(
            width, 
            title_height + padding, 
            colors["primary"], 
            colors["background"], 
            vertical=True
        )
        image.paste(header_gradient, (0, 0))
        
        # Draw title
        draw.text(
            (padding, padding), 
            title, 
            font=fonts["title"], 
            fill=colors["background"]
        )
        
        # Draw current date
        date_str = now.strftime("%A, %B %d, %Y")
        draw.text(
            (width - padding, padding), 
            date_str, 
            font=fonts["date"], 
            fill=colors["background"],
            anchor="ra"
        )
        
        # Draw main content background with rounded corners
        content_top = title_height + padding * 1.5
        content_height = height - content_top - padding
        
        self._draw_rounded_rectangle(
            draw,
            [(padding, content_top), (width - padding, height - padding)],
            colors["secondary"],
            radius=10
        )
        
        # Adjust for the content interior padding
        inner_padding = 15
        content_list_top = content_top + inner_padding
        content_width = width - padding * 2 - inner_padding * 2
        content_area_height = content_height - inner_padding * 2
        
        line_height = int(content_area_height / 8)  # Allow approximately 8 items
        
        # Draw "Upcoming Events" header
        events_header_y = content_list_top + 5
        draw.text(
            (padding + inner_padding, events_header_y), 
            "UPCOMING EVENTS", 
            font=fonts["header"], 
            fill=colors["primary"]
        )
        
        # Draw decorative line
        header_width = fonts["header"].getbbox("UPCOMING EVENTS")[2] if fonts["header"] else 150
        draw.line(
            [(padding + inner_padding, events_header_y + 30), 
             (padding + inner_padding + header_width, events_header_y + 30)],
            fill=colors["primary"],
            width=2
        )
        
        y_pos = content_list_top + 50  # Start below the header
        
        if not events:
            # No events
            no_events_y = content_list_top + content_area_height / 2
            
            # Draw decorative elements
            dash_width = 20
            dash_spacing = 10
            dash_count = 10
            dash_total_width = dash_count * (dash_width + dash_spacing) - dash_spacing
            dash_start_x = (width - dash_total_width) / 2
            
            for i in range(dash_count):
                dash_x = dash_start_x + i * (dash_width + dash_spacing)
                draw.line(
                    [(dash_x, no_events_y - 30), (dash_x + dash_width, no_events_y - 30)],
                    fill=colors["text_light"],
                    width=2
                )
            
            draw.text(
                (width / 2, no_events_y), 
                "No upcoming events", 
                font=fonts["header"], 
                fill=colors["text_medium"],
                anchor="mm"
            )
            
            # Draw another decorative element
            for i in range(dash_count):
                dash_x = dash_start_x + i * (dash_width + dash_spacing)
                draw.line(
                    [(dash_x, no_events_y + 30), (dash_x + dash_width, no_events_y + 30)],
                    fill=colors["text_light"],
                    width=2
                )
        else:
            # Group events by day
            current_day = None
            for event in events:
                event_day = event['start'].date() if isinstance(event['start'], datetime) else event['start']
                
                # Print day header if it's a new day
                if event_day != current_day:
                    current_day = event_day
                    
                    # Format day string based on how far in the future it is
                    today = now.date()
                    if event_day == today:
                        day_str = "TODAY"
                    elif event_day == today + timedelta(days=1):
                        day_str = "TOMORROW"
                    else:
                        day_str = event_day.strftime("%A, %B %d").upper()
                    
                    # Add more space before new day header (except the first one)
                    if y_pos > content_list_top + 50:
                        y_pos += line_height / 2
                    
                    # Draw day header in a pill-shaped badge
                    day_header_width = fonts["header"].getbbox(day_str)[2] + 20 if fonts["header"] else 150
                    self._draw_rounded_rectangle(
                        draw,
                        [(padding + inner_padding, y_pos - 5),
                         (padding + inner_padding + day_header_width, y_pos + 25)],
                        colors["primary"],
                        radius=15
                    )
                    
                    draw.text(
                        (padding + inner_padding + 10, y_pos + 10), 
                        day_str, 
                        font=fonts["small"], 
                        fill=colors["background"],
                        anchor="lm"
                    )
                    
                    y_pos += 40  # Add space after the day header
                
                # Format time
                if event['all_day']:
                    time_str = "ALL DAY"
                else:
                    if isinstance(event['start'], datetime):
                        time_str = event['start'].strftime("%I:%M %p")
                    else:
                        time_str = "ALL DAY"
                
                # Draw event container
                event_height = line_height + 10
                self._draw_rounded_rectangle(
                    draw,
                    [(padding + inner_padding, y_pos - 5),
                     (width - padding - inner_padding, y_pos + event_height)],
                    colors["event_bg"],
                    radius=8
                )
                
                # Draw time in a small box
                time_width = fonts["time"].getbbox(time_str)[2] + 20 if fonts["time"] else 80
                self._draw_rounded_rectangle(
                    draw,
                    [(padding + inner_padding + 10, y_pos + 5),
                     (padding + inner_padding + 10 + time_width, y_pos + 30)],
                    colors["primary"] if not event['all_day'] else colors["highlight"],
                    radius=5
                )
                
                draw.text(
                    (padding + inner_padding + 10 + time_width/2, y_pos + 17), 
                    time_str, 
                    font=fonts["small"], 
                    fill=colors["background"],
                    anchor="mm"
                )
                
                # Draw event summary, potentially truncated
                summary = event['summary']
                if fonts["event"]:
                    max_width = width - padding * 2 - 100
                    if fonts["event"].getbbox(summary)[2] > max_width:
                        # Truncate the text
                        chars_that_fit = max_width // fonts["event"].getbbox("A")[2]
                        summary = summary[:chars_that_fit - 3] + "..."
                
                draw.text(
                    (padding + inner_padding + 20 + time_width, y_pos + 17), 
                    summary, 
                    font=fonts["event"], 
                    fill=colors["text_dark"],
                    anchor="lm"
                )
                
                # Add location if available and there's space
                if event['location'] and event['location'] != '':
                    location_text = event['location']
                    if fonts["small"]:
                        max_width = width - padding * 2 - 100
                        if fonts["small"].getbbox(location_text)[2] > max_width:
                            # Truncate the text
                            chars_that_fit = max_width // fonts["small"].getbbox("A")[2]
                            location_text = location_text[:chars_that_fit - 3] + "..."
                    
                    draw.text(
                        (padding + inner_padding + 20 + time_width, y_pos + 42), 
                        location_text, 
                        font=fonts["small"], 
                        fill=colors["text_light"],
                        anchor="lm"
                    )
                    
                    # Increase height for events with location
                    event_height += 20
                    
                # Add some space between events
                y_pos += event_height + 5
                
                # Stop if we've reached the bottom of the image
                if y_pos > height - padding - inner_padding:
                    # Draw a "more events" indicator if there are more events
                    if events.index(event) < len(events) - 1:
                        more_count = len(events) - events.index(event) - 1
                        more_text = f"+{more_count} more event{'s' if more_count > 1 else ''}"
                        
                        draw.text(
                            (width / 2, height - padding - inner_padding / 2), 
                            more_text, 
                            font=fonts["small"], 
                            fill=colors["text_medium"],
                            anchor="mm"
                        )
                    break
        
        return image
    
    def render_week_view(self, dimensions, title, events, now, color_scheme="blue"):
        """Render the calendar events in a weekly grid."""
        width, height = dimensions
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["blue"])
        
        # Create the image
        image = Image.new("RGBA", dimensions, colors["background"])
        draw = ImageDraw.Draw(image)
        
        # Get fonts
        fonts = self.get_fonts(width)
        
        # Calculate dimensions
        padding = int(width * 0.03)
        title_height = int(height * 0.12)
        
        # Calculate the week start date (start on Sunday or Monday)
        today = now.date()
        week_start = today - timedelta(days=today.weekday())  # Monday as first day
        
        # Create a gradient header background
        header_gradient = self._create_gradient(
            width, 
            title_height, 
            colors["primary"], 
            (colors["primary"][0], colors["primary"][1], colors["primary"][2], 80), 
            vertical=True
        )
        image.paste(header_gradient, (0, 0))
        
        # Draw title
        draw.text(
            (padding, padding), 
            title, 
            font=fonts["title"], 
            fill=colors["background"]
        )
        
        # Draw month/year
        month_year = now.strftime("%B %Y")
        draw.text(
            (width - padding, padding), 
            month_year, 
            font=fonts["date"], 
            fill=colors["background"],
            anchor="ra"
        )
        
        # Calculate grid dimensions
        grid_top = title_height + padding
        grid_width = width - padding * 2
        grid_height = height - grid_top - padding
        cell_width = grid_width / 7
        
        # Draw main content background with rounded corners
        self._draw_rounded_rectangle(
            draw,
            [(padding, grid_top), (width - padding, height - padding)],
            colors["secondary"],
            radius=10
        )
        
        # Adjust grid top to account for the background
        grid_top += int(padding * 0.5)
        
        # Draw day headers in a separate bar
        header_height = int(grid_height * 0.1)
        header_bar_top = grid_top
        header_bar_bottom = grid_top + header_height
        
        # Draw header bar
        draw.rectangle(
            [(padding + 5, header_bar_top), (width - padding - 5, header_bar_bottom)],
            fill=colors["primary"]
        )
        
        # Draw day headers
        days = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        for i, day in enumerate(days):
            x = padding + 5 + i * cell_width + cell_width / 2
            y = header_bar_top + header_height / 2
            draw.text(
                (x, y), 
                day, 
                font=fonts["small"], 
                fill=colors["background"],
                anchor="mm"
            )
        
        # Calculate cell height for days
        days_grid_top = header_bar_bottom + 5
        days_grid_height = grid_height - header_height - 10
        cell_height = days_grid_height / 5  # 5 weeks max
        
        # Organize events by day
        events_by_day = {}
        for event in events:
            event_day = event['start'].date() if isinstance(event['start'], datetime) else event['start']
            if event_day not in events_by_day:
                events_by_day[event_day] = []
            events_by_day[event_day].append(event)
        
        # Determine the dates to display (35 days / 5 weeks)
        dates = []
        for i in range(35):
            dates.append(week_start + timedelta(days=i))
        
        # Draw the date grid
        for i, date in enumerate(dates):
            row = i // 7
            col = i % 7
            
            x = padding + 5 + col * cell_width
            y = days_grid_top + row * cell_height
            
            # Determine cell colors
            is_current_month = date.month == today.month
            cell_bg_color = colors["background"] if is_current_month else (
                colors["secondary"][0], 
                colors["secondary"][1], 
                colors["secondary"][2], 
                int(colors["secondary"][3] * 0.7)
            )
            
            # Draw cell background
            draw.rectangle(
                [(x, y), (x + cell_width - 1, y + cell_height - 1)],
                fill=cell_bg_color,
                outline=colors["grid_lines"],
                width=1
            )
            
            # Highlight today with a circle
            if date == today:
                # Draw a highlighted circle around today's date
                circle_x = x + 18
                circle_y = y + 18
                circle_radius = 15
                
                draw.ellipse(
                    [(circle_x - circle_radius, circle_y - circle_radius),
                     (circle_x + circle_radius, circle_y + circle_radius)],
                    fill=colors["highlight"]
                )
                
                # Draw the date number in white for better contrast
                draw.text(
                    (circle_x, circle_y), 
                    str(date.day), 
                    font=fonts["small"], 
                    fill=colors["background"],
                    anchor="mm"
                )
            else:
                # Draw regular date number
                date_color = colors["text_dark"] if is_current_month else colors["text_light"]
                draw.text(
                    (x + 18, y + 18), 
                    str(date.day), 
                    font=fonts["small"], 
                    fill=date_color,
                    anchor="mm"
                )
            
            # Draw events for this day
            if date in events_by_day:
                day_events = events_by_day[date]
                event_y = y + 36  # Start below the date number
                
                # Show at most 3 events per day in the grid
                for j, event in enumerate(day_events[:3]):
                    if j >= 3 or event_y + 12 > y + cell_height - 5:
                        break
                        
                    event_height = min(20, (cell_height - 45) / 3)
                    
                    # Draw a small colored event indicator
                    event_width = cell_width - 15
                    
                    # Determine event color
                    event_color = colors["primary"]
                    if event['all_day']:
                        # Use a slightly different color for all-day events
                        event_color = (
                            min(255, event_color[0] + 30),
                            min(255, event_color[1] + 30),
                            min(255, event_color[2] + 30),
                            event_color[3]
                        )
                    
                    # Draw rounded event indicator
                    self._draw_rounded_rectangle(
                        draw,
                        [(x + 5, event_y), (x + 5 + event_width, event_y + event_height)],
                        event_color,
                        radius=3
                    )
                    
                    # Show truncated event title if it fits
                    if fonts["small"] and event_height >= 12:
                        event_title = event['summary']
                        if len(event_title) > 10:
                            event_title = event_title[:8] + ".."
                        
                        draw.text(
                            (x + 10, event_y + event_height/2), 
                            event_title, 
                            font=fonts["small"], 
                            fill=colors["background"],
                            anchor="lm"
                        )
                    
                    event_y += event_height + 2
                
                # Show indicator for more events
                if len(day_events) > 3:
                    more_y = y + cell_height - 15
                    more_text = f"+{len(day_events) - 3}"
                    
                    # Draw small indicator dot
                    dot_radius = 3
                    dot_x = x + cell_width / 2 - 10
                    draw.ellipse(
                        [(dot_x - dot_radius, more_y - dot_radius),
                         (dot_x + dot_radius, more_y + dot_radius)],
                        fill=colors["highlight"]
                    )
                    
                    draw.text(
                        (x + cell_width/2, more_y), 
                        more_text, 
                        font=fonts["small"], 
                        fill=colors["text_medium"],
                        anchor="mm"
                    )
        
        # Add a visual footer with upcoming events
        if events:
            # Find the next 3 upcoming events
            now_dt = datetime.now(pytz.UTC)
            upcoming_events = [e for e in events if 
                               ((isinstance(e['start'], datetime) and e['start'] > now_dt) or
                               (isinstance(e['start'], dt_date) and e['start'] > today))
                              ]
            upcoming_events.sort(key=lambda x: x['start'])
            upcoming_events = upcoming_events[:3]
            
            if upcoming_events:
                footer_y = height - int(padding * 1.5)
                
                # Draw a "Coming up" text
                draw.text(
                    (padding + 15, footer_y), 
                    "COMING UP:", 
                    font=fonts["small"], 
                    fill=colors["primary"],
                    anchor="lm"
                )
                
                # Format event info
                event_infos = []
                for event in upcoming_events:
                    if isinstance(event['start'], datetime):
                        date_str = event['start'].strftime("%a %d, %H:%M")
                    else:
                        date_str = event['start'].strftime("%a %d")
                        
                    event_str = f"{date_str} - {event['summary']}"
                    if len(event_str) > 40:
                        event_str = event_str[:37] + "..."
                    event_infos.append(event_str)
                
                # Join event info and draw
                events_text = " | ".join(event_infos)
                if len(events_text) > width // 10:  # If too long, truncate
                    events_text = events_text[:width // 10 - 3] + "..."
                    
                draw.text(
                    (padding + 120, footer_y), 
                    events_text, 
                    font=fonts["small"], 
                    fill=colors["text_medium"],
                    anchor="lm"
                )
        
        return image
    
    def render_day_view(self, dimensions, title, events, now, color_scheme="blue"):
        """Render a detailed day view showing hourly schedule."""
        width, height = dimensions
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES["blue"])
        
        # Create the image
        image = Image.new("RGBA", dimensions, colors["background"])
        draw = ImageDraw.Draw(image)
        
        # Get fonts
        fonts = self.get_fonts(width)
        
        # Calculate dimensions
        padding = int(width * 0.03)
        title_height = int(height * 0.1)
        
        # Filter events for today
        today = now.date()
        today_events = [e for e in events if 
                        (isinstance(e['start'], datetime) and e['start'].date() == today) or
                        (isinstance(e['start'], dt_date) and e['start'] == today)]
        
        # Sort by start time
        today_events.sort(key=lambda x: (x['all_day'], x['start']))
        
        # Draw a decorative header with gradient
        header_height = int(height * 0.15)
        header_gradient = self._create_gradient(
            width, 
            header_height, 
            colors["primary"], 
            colors["background"], 
            vertical=True
        )
        image.paste(header_gradient, (0, 0))
        
        # Draw month and year in the header
        month_year = now.strftime("%B %Y").upper()
        draw.text(
            (width // 2, padding + 5), 
            month_year, 
            font=fonts["header"], 
            fill=colors["background"],
            anchor="mt"
        )
        
        # Draw current date in a circle in the header
        circle_size = int(min(width, height) * 0.12)
        circle_center = (width // 2, header_height // 2 + padding + 15)
        
        # Draw circle
        draw.ellipse(
            [(circle_center[0] - circle_size//2, circle_center[1] - circle_size//2),
             (circle_center[0] + circle_size//2, circle_center[1] + circle_size//2)],
            fill=colors["background"],
            outline=colors["highlight"],
            width=2
        )
        
        # Draw day number in circle
        day_num = now.strftime("%d")
        draw.text(
            circle_center, 
            day_num, 
            font=fonts["title"], 
            fill=colors["primary"],
            anchor="mm"
        )
        
        # Draw day name below the circle
        day_name = now.strftime("%A")
        draw.text(
            (width // 2, circle_center[1] + circle_size//2 + 10), 
            day_name, 
            font=fonts["date"], 
            fill=colors["text_dark"],
            anchor="mt"
        )
        
        # Calculate schedule area dimensions
        schedule_top = header_height + padding * 2
        schedule_width = width - padding * 2
        schedule_height = height - schedule_top - padding
        
        # Draw schedule background with rounded corners
        self._draw_rounded_rectangle(
            draw,
            [(padding, schedule_top), (width - padding, height - padding)],
            colors["secondary"],
            radius=15
        )
        
        # Draw all-day events first
        all_day_events = [e for e in today_events if e['all_day']]
        all_day_height = 0
        
        if all_day_events:
            # Calculate height needed for all-day events
            all_day_height = min(len(all_day_events) * 30, 80)
            
            # Draw all-day header
            draw.rectangle(
                [(padding + 10, schedule_top + 10), (width - padding - 10, schedule_top + 35)],
                fill=colors["primary"]
            )
            draw.text(
                (padding + 25, schedule_top + 22), 
                "ALL DAY", 
                font=fonts["header"], 
                fill=colors["background"],
                anchor="lm"
            )
            
            # Draw each all-day event
            for i, event in enumerate(all_day_events):
                y = schedule_top + 45 + i * 30
                
                # Draw event indicator dot
                dot_radius = 5
                draw.ellipse(
                    [(padding + 20 - dot_radius, y - dot_radius), 
                     (padding + 20 + dot_radius, y + dot_radius)],
                    fill=colors["highlight"]
                )
                
                # Draw event title with truncation if needed
                event_title = event['summary']
                if fonts["event"] and len(event_title) * fonts["event"].getbbox("A")[2] > width - padding * 5:
                    truncate_length = (width - padding * 5) // fonts["event"].getbbox("A")[2]
                    event_title = event_title[:truncate_length - 3] + "..."
                
                draw.text(
                    (padding + 40, y), 
                    event_title, 
                    font=fonts["event"], 
                    fill=colors["text_dark"]
                )
        
        # Calculate hourly schedule area
        hourly_top = schedule_top + (all_day_height if all_day_events else 0) + 20
        hourly_height = schedule_height - (all_day_height if all_day_events else 0) - 20
        hour_height = hourly_height / 12  # Show 12 hours
        
        # Determine start hour (8am or current hour, whichever is earlier)
        start_hour = min(8, now.hour)
        
        # Draw time slots with alternating colors
        for i in range(12):
            hour = (start_hour + i) % 24
            y = hourly_top + i * hour_height
            
            # Draw hour background with alternating colors
            draw.rectangle(
                [(padding + 10, y), (width - padding - 10, y + hour_height)],
                fill=colors["background"] if i % 2 == 0 else colors["secondary"]
            )
            
            # Format hour string
            hour_str = f"{hour if hour != 0 else 12}:00"
            am_pm = "AM" if hour < 12 else "PM"
            time_str = f"{hour_str} {am_pm}"
            
            # Draw time in a small circular badge
            time_circle_radius = int(hour_height * 0.25)
            draw.ellipse(
                [(padding + 30 - time_circle_radius, y + hour_height/2 - time_circle_radius),
                 (padding + 30 + time_circle_radius, y + hour_height/2 + time_circle_radius)],
                fill=colors["primary"]
            )
            
            draw.text(
                (padding + 30, y + hour_height/2), 
                str(hour if hour != 0 else 12), 
                font=fonts["small"], 
                fill=colors["background"],
                anchor="mm"
            )
            
            # Draw AM/PM
            draw.text(
                (padding + 30, y + hour_height/2 + time_circle_radius + 5), 
                am_pm, 
                font=fonts["small"], 
                fill=colors["text_medium"],
                anchor="mt"
            )
            
            # Draw horizontal grid line
            draw.line(
                [(padding + 60, y + hour_height), (width - padding - 10, y + hour_height)], 
                fill=colors["grid_lines"], 
                width=1
            )
        
        # Draw timed events
        timed_events = [e for e in today_events if not e['all_day']]
        for event in timed_events:
            if not isinstance(event['start'], datetime):
                continue
                
            event_start = event['start']
            event_end = event['end'] if isinstance(event['end'], datetime) else event_start + timedelta(hours=1)
            
            # Calculate position
            start_y_pos = hourly_top + (event_start.hour - start_hour + event_start.minute / 60) * hour_height
            end_y_pos = hourly_top + (event_end.hour - start_hour + event_end.minute / 60) * hour_height
            event_height = max(end_y_pos - start_y_pos, 40)  # Min height of 40px
            
            # Ensure event is in the visible range
            if start_y_pos > hourly_top + hourly_height or end_y_pos < hourly_top:
                continue
            
            # Adjust if event starts before visible range
            if start_y_pos < hourly_top:
                start_y_pos = hourly_top
            
            # Adjust if event ends after visible range
            if end_y_pos > hourly_top + hourly_height:
                end_y_pos = hourly_top + hourly_height
                
            # Draw event with rounded corners
            self._draw_rounded_rectangle(
                draw,
                [(padding + 70, start_y_pos), (width - padding - 20, start_y_pos + event_height)],
                colors["event_bg"],
                radius=8,
                outline=colors["primary"],
                width=2
            )
            
            # Format time
            time_str = event_start.strftime("%I:%M %p")
            if event_end.day == event_start.day:
                time_str += f" - {event_end.strftime('%I:%M %p')}"
            
            # Draw time and title
            draw.text(
                (padding + 85, start_y_pos + 10), 
                time_str, 
                font=fonts["small"], 
                fill=colors["text_medium"]
            )
            
            # Draw event summary, potentially truncated
            summary = event['summary']
            if fonts["event"]:
                max_width = width - padding * 2 - 100
                if fonts["event"].getbbox(summary)[2] > max_width:
                    # Truncate the text
                    chars_that_fit = max_width // fonts["event"].getbbox("A")[2]
                    summary = summary[:chars_that_fit - 3] + "..."
            
            draw.text(
                (padding + 85, start_y_pos + 30), 
                summary, 
                font=fonts["event"], 
                fill=colors["text_dark"]
            )
            
            # Draw location if available
            if event['location'] and event['location'] != '' and event_height > 60:
                location_text = event['location']
                if fonts["small"]:
                    max_width = width - padding * 2 - 100
                    if fonts["small"].getbbox(location_text)[2] > max_width:
                        # Truncate the text
                        chars_that_fit = max_width // fonts["small"].getbbox("A")[2]
                        location_text = location_text[:chars_that_fit - 3] + "..."
                
                draw.text(
                    (padding + 85, start_y_pos + 55), 
                    location_text, 
                    font=fonts["small"], 
                    fill=colors["text_light"]
                )
        
        # Draw "No events" if no events for today
        if not today_events:
            no_events_y = (schedule_top + height - padding) / 2
            
            # Draw a decorative element for "no events"
            dash_width = 20
            dash_spacing = 10
            dash_count = 10
            dash_total_width = dash_count * (dash_width + dash_spacing) - dash_spacing
            dash_start_x = (width - dash_total_width) / 2
            
            for i in range(dash_count):
                dash_x = dash_start_x + i * (dash_width + dash_spacing)
                draw.line(
                    [(dash_x, no_events_y - 30), (dash_x + dash_width, no_events_y - 30)],
                    fill=colors["text_light"],
                    width=2
                )
            
            draw.text(
                (width / 2, no_events_y), 
                "No events scheduled for today", 
                font=fonts["header"], 
                fill=colors["text_medium"],
                anchor="mm"
            )
            
            # Draw another decorative element
            for i in range(dash_count):
                dash_x = dash_start_x + i * (dash_width + dash_spacing)
                draw.line(
                    [(dash_x, no_events_y + 30), (dash_x + dash_width, no_events_y + 30)],
                    fill=colors["text_light"],
                    width=2
                )
        
        return image
    
    def _create_gradient(self, width, height, start_color, end_color, vertical=False):
        """Create a gradient image."""
        # Create a new image
        gradient = Image.new('RGBA', (width, height), end_color)
        draw = ImageDraw.Draw(gradient)
        
        # Create gradient
        for i in range(height if vertical else width):
            # Calculate color at this position
            ratio = i / (height if vertical else width)
            r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
            g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
            b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
            a = int(start_color[3] * (1 - ratio) + end_color[3] * ratio)
            
            # Draw a line of the gradient color
            if vertical:
                draw.line([(0, i), (width, i)], fill=(r, g, b, a))
            else:
                draw.line([(i, 0), (i, height)], fill=(r, g, b, a))
                
        return gradient
        
    def _draw_rounded_rectangle(self, draw, xy, fill=None, radius=10, outline=None, width=1):
        """Draw a rounded rectangle."""
        x1, y1 = xy[0]
        x2, y2 = xy[1]
        
        # Draw four corners
        draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill, outline=outline, width=width)
        draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 0, fill=fill, outline=outline, width=width)
        draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill, outline=outline, width=width)
        draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill, outline=outline, width=width)
        
        # Draw four sides
        draw.rectangle([x1 + radius, y1, x2 - radius, y1 + radius], fill=fill, outline=outline if width == 1 else None)
        draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=outline if width == 1 else None)
        draw.rectangle([x1 + radius, y2 - radius, x2 - radius, y2], fill=fill, outline=outline if width == 1 else None)
        
        # Draw outline if width > 1
        if outline and width > 1:
            for i in range(width):
                # Draw outline for the rounded rectangle
                offset = i
                draw.arc([x1 + offset, y1 + offset, x1 + radius * 2 - offset, y1 + radius * 2 - offset], 180, 270, outline)
                draw.arc([x2 - radius * 2 + offset, y1 + offset, x2 - offset, y1 + radius * 2 - offset], 270, 0, outline)
                draw.arc([x1 + offset, y2 - radius * 2 + offset, x1 + radius * 2 - offset, y2 - offset], 90, 180, outline)
                draw.arc([x2 - radius * 2 + offset, y2 - radius * 2 + offset, x2 - offset, y2 - offset], 0, 90, outline)
                
                # Draw lines connecting the arcs
                draw.line([(x1 + radius, y1 + offset), (x2 - radius, y1 + offset)], outline)
                draw.line([(x1 + offset, y1 + radius), (x1 + offset, y2 - radius)], outline)
                draw.line([(x2 - offset, y1 + radius), (x2 - offset, y2 - radius)], outline)
                draw.line([(x1 + radius, y2 - offset), (x2 - radius, y2 - offset)], outline)
    
    def render_error_image(self, dimensions, error_message):
        """Render an error message as an image."""
        width, height = dimensions
        
        # Create a blank image with white background
        image = Image.new("RGBA", dimensions, (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        
        # Get fonts
        fonts = self.get_fonts(width)
        
        # Draw title
        draw.text(
            (width * 0.05, height * 0.05), 
            "Calendar Error", 
            font=fonts["title"], 
            fill=(0, 0, 0, 255)
        )
        
        # Draw separator line
        draw.line(
            [(width * 0.05, height * 0.2), (width * 0.95, height * 0.2)], 
            fill=(200, 200, 200, 255), 
            width=2
        )
        
        # Draw error message
        draw.text(
            (width * 0.05, height * 0.3), 
            error_message, 
            font=fonts["event"], 
            fill=(0, 0, 0, 255)
        )
        
        return image 