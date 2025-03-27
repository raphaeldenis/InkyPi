import os
from datetime import datetime, timedelta, date as dt_date
import logging
from io import BytesIO
import pytz
import urllib.request
from icalendar import Calendar
import recurring_ical_events
from PIL import Image, ImageDraw, ImageFont
from utils.app_utils import get_font, resolve_path
from plugins.base_plugin.base_plugin import BasePlugin
import re
import calendar
import subprocess
import tempfile

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
        "text_header": (255, 255, 255, 255),
        "gradient_primary": "linear-gradient(to bottom, rgba(65, 105, 225, 1), rgba(30, 144, 255, 1))",
        "gradient_secondary": "linear-gradient(to right, rgba(240, 248, 255, 1), rgba(220, 240, 255, 1))"
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
        "text_header": (255, 255, 255, 255),
        "gradient_primary": "linear-gradient(to bottom, rgba(50, 50, 50, 1), rgba(20, 20, 20, 1))",
        "gradient_secondary": "linear-gradient(to right, rgba(80, 80, 80, 1), rgba(60, 60, 60, 1))"
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
        "text_header": (255, 255, 255, 255),
        "gradient_primary": "linear-gradient(to bottom, rgba(46, 139, 87, 1), rgba(60, 179, 113, 1))",
        "gradient_secondary": "linear-gradient(to right, rgba(240, 255, 240, 1), rgba(220, 255, 220, 1))"
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
        "text_header": (255, 255, 255, 255),
        "gradient_primary": "linear-gradient(to bottom, rgba(106, 90, 205, 1), rgba(147, 112, 219, 1))",
        "gradient_secondary": "linear-gradient(to right, rgba(248, 240, 255, 1), rgba(240, 230, 255, 1))"
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
        "text_header": (255, 255, 255, 255),
        "gradient_primary": "linear-gradient(to bottom, rgba(0, 0, 0, 1), rgba(40, 40, 40, 1))",
        "gradient_secondary": "linear-gradient(to right, rgba(245, 245, 245, 1), rgba(230, 230, 230, 1))"
    }
}

class ICalendar(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        # Get settings
        url = settings.get('calendarUrl', '')
        days_to_show = int(settings.get('daysToShow', DEFAULT_DAYS_TO_SHOW))
        max_events = int(settings.get('maxEvents', DEFAULT_MAX_EVENTS))
        title = settings.get('title', 'Calendar')
        view_mode = settings.get('viewMode', DEFAULT_VIEW_MODE)
        color_scheme = settings.get('colorScheme', 'blue')
        
        # Get display dimensions - ensure we have integer values
        dimensions = device_config.get_resolution()
        width, height = int(dimensions[0]), int(dimensions[1])
        
        if device_config.get_config("orientation") == "vertical":
            width, height = height, width
        
        # Use the integer dimensions
        dimensions = (width, height)
        
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
            
            # Prepare the template data based on view mode
            template_data = {
                'title': title,
                'current_date': now.strftime("%A, %B %d"),
                'view_mode': view_mode,  # Explicitly include view_mode
                'plugin_settings': settings
            }
            
            # Add CSS variables for theming
            css_vars = self.get_css_variables(color_scheme)
            template_data.update(css_vars)
            
            # Add view-specific data
            if view_mode == "day":
                template_data.update(self.prepare_day_view_data(events, now, tz))
            elif view_mode == "week":
                template_data.update(self.prepare_week_view_data(events, now, tz))
            else:  # Default to list view
                template_data.update(self.prepare_list_view_data(events, now, tz))
            
            # Prepare a temp output path for generation
            output_path = resolve_path("calendar_temp.png")
            
            try:
                # Try using HTML rendering first
                image = self.render_html(output_path, template_data, dimensions)
                return image
            except Exception as e:
                # If HTML rendering fails, try direct rendering
                logger.warning(f"HTML rendering failed: {str(e)}, falling back to direct rendering")
                return self.render_direct(output_path, template_data, width, height)
                
        except Exception as e:
            logger.error(f"Failed to generate calendar image: {str(e)}")
            return self.render_error_image(dimensions, str(e))
    
    def get_css_variables(self, color_scheme):
        """Convert color scheme to CSS variables."""
        if color_scheme not in COLOR_SCHEMES:
            color_scheme = "blue"  # Default fallback
            
        scheme = COLOR_SCHEMES[color_scheme]
        css_vars = {}
        
        for key, value in scheme.items():
            if isinstance(value, tuple):
                # Convert RGBA to CSS format
                if len(value) == 4:
                    css_vars[f"--{key}"] = f"rgba({value[0]}, {value[1]}, {value[2]}, {value[3]/255})"
                else:
                    css_vars[f"--{key}"] = f"rgb({value[0]}, {value[1]}, {value[2]})"
            else:
                css_vars[f"--{key}"] = value
                
        return css_vars
    
    def prepare_day_view_data(self, events, now, tz):
        """Prepare data for day view template."""
        # Filter events for today
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        
        day_events = []
        all_day_events = []
        
        try:
            for event in events:
                event_start = event['start']
                event_end = event['end']
                
                # Check if event is today
                if isinstance(event_start, datetime):
                    event_start_date = event_start.date()
                else:
                    event_start_date = event_start
                    
                if event_start_date != today.date():
                    continue
                    
                if event['all_day']:
                    all_day_events.append({
                        'summary': event['summary'],
                        'location': event['location'],
                        'all_day': True
                    })
                else:
                    # Calculate position and height for the event
                    try:
                        start_hour = float(event_start.hour + event_start.minute / 60)
                        
                        # Handle events that end on the next day
                        if event_end.date() > today.date():
                            end_hour = 24.0  # End at midnight if extends to tomorrow
                        else:
                            end_hour = float(event_end.hour + event_end.minute / 60)
                        
                        # Format time
                        start_time = event_start.strftime("%-I:%M %p")
                        end_time = event_end.strftime("%-I:%M %p")
                        time_str = f"{start_time} - {end_time}"
                        
                        day_events.append({
                            'summary': event['summary'],
                            'location': event['location'],
                            'start_time': start_time,
                            'end_time': end_time,
                            'time': time_str,
                            'all_day': False
                        })
                    except (AttributeError, TypeError) as e:
                        logger.warning(f"Error formatting event time: {str(e)} for event {event['summary']}")
                        continue
        except Exception as e:
            logger.error(f"Error preparing day view data: {str(e)}")
        
        # Generate hour labels for the day
        hours = []
        for hour in range(0, 24):
            hour_str = datetime(2000, 1, 1, hour).strftime("%-I %p").lower()
            hours.append(hour_str)
        
        return {
            'current_date': today.strftime("%B %d"),
            'day_name': today.strftime("%A"),
            'hours': hours,
            'current_hour': now.strftime("%H:00"),
            'events': day_events,
            'all_day_events': all_day_events
        }
    
    def prepare_week_view_data(self, events, now, tz):
        """Prepare data for week view template."""
        # Get the current month's calendar
        cal = calendar.monthcalendar(now.year, now.month)
        
        # Find today in the calendar
        today_day = now.day
        today_month = now.month
        today_year = now.year
        
        # Prepare calendar grid with additional info
        calendar_grid = []
        month_start = datetime(now.year, now.month, 1, tzinfo=tz)
        prev_month = month_start - timedelta(days=1)
        next_month = month_start.replace(day=28) + timedelta(days=4)
        next_month = next_month.replace(day=1)
        
        # Weekday abbreviations
        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        
        # Process each week
        for week in cal:
            week_data = []
            for i, day in enumerate(week):
                if day == 0:
                    # Day from previous/next month
                    if len(week_data) == 0:
                        # Previous month
                        last_day_prev = prev_month.replace(day=calendar.monthrange(prev_month.year, prev_month.month)[1])
                        day_offset = 6 - last_day_prev.weekday()
                        target_day = last_day_prev - timedelta(days=day_offset - i)
                        day_info = {
                            'day': target_day.day,
                            'date': target_day.strftime("%Y-%m-%d"),
                            'today': False,
                            'different_month': True,
                            'has_events': self._has_events_on_date(events, target_day.date())
                        }
                    else:
                        # Next month
                        target_day = next_month.replace(day=1 + i - week.index(0))
                        day_info = {
                            'day': target_day.day,
                            'date': target_day.strftime("%Y-%m-%d"),
                            'today': False,
                            'different_month': True,
                            'has_events': self._has_events_on_date(events, target_day.date())
                        }
                else:
                    # Day in current month
                    target_day = datetime(now.year, now.month, day, tzinfo=tz)
                    day_info = {
                        'day': day,
                        'date': target_day.strftime("%Y-%m-%d"),
                        'today': (day == today_day and now.month == today_month and now.year == today_year),
                        'different_month': False,
                        'has_events': self._has_events_on_date(events, target_day.date())
                    }
                week_data.append(day_info)
            calendar_grid.append(week_data)
        
        # Prepare upcoming events
        upcoming_events = []
        
        # Number of days to look ahead
        lookahead = 14
        
        # Define date range
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=lookahead)
        
        # Group events by day
        sorted_events = sorted(events, key=lambda x: x['start'])
        
        for event in sorted_events:
            event_start = event['start']
            
            # Skip past events
            if isinstance(event_start, datetime) and event_start < now:
                continue
                
            # Convert date to datetime for comparison if needed
            if not isinstance(event_start, datetime):
                event_start = datetime.combine(event_start, datetime.min.time(), tzinfo=tz)
            
            # Skip events too far in the future
            if event_start.date() > end_date.date():
                continue
                
            # Format date/time for display
            event_day = event_start.strftime("%a %d")
            
            if event['all_day']:
                event_time = ""
            else:
                event_time = event_start.strftime("%-I:%M %p")
                
            upcoming_events.append({
                'day': event_day,
                'time': event_time,
                'summary': event['summary'],
                'location': event['location'],
                'all_day': event['all_day']
            })
            
            # Limit to 10 upcoming events
            if len(upcoming_events) >= 10:
                break
        
        return {
            'month_name': now.strftime("%B %Y"),
            'weekdays': weekdays,
            'calendar_grid': calendar_grid,
            'upcoming_events': upcoming_events
        }
    
    def _has_events_on_date(self, events, date):
        """Check if there are events on a specific date."""
        for event in events:
            event_start = event['start']
            event_end = event['end']
            
            # Convert to date if datetime
            if isinstance(event_start, datetime):
                event_start = event_start.date()
            if isinstance(event_end, datetime):
                event_end = event_end.date()
                
            # Check if date falls within event range
            if event_start <= date <= event_end:
                return True
                
        return False
    
    def prepare_list_view_data(self, events, now, tz):
        """Prepare data for list view template."""
        # Group events by day
        days_dict = {}
        
        # Number of days to look ahead
        lookahead = 14
        
        # Define date range
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=lookahead)
        
        for event in events:
            event_start = event['start']
            
            # Convert date to datetime for comparison if needed
            if not isinstance(event_start, datetime):
                event_start_dt = datetime.combine(event_start, datetime.min.time(), tzinfo=tz)
            else:
                event_start_dt = event_start
                
            # Skip events outside our range
            if event_start_dt.date() < start_date.date() or event_start_dt.date() > end_date.date():
                continue
                
            # Get date string as key
            date_key = event_start_dt.date().isoformat()
            
            # Initialize day entry if needed
            if date_key not in days_dict:
                day_info = {
                    'name': event_start_dt.strftime("%A"),
                    'date': event_start_dt.strftime("%B %d"),
                    'events': []
                }
                days_dict[date_key] = day_info
                
            # Format times
            if event['all_day']:
                start_time = ""
                end_time = ""
            else:
                start_time = event_start.strftime("%-I:%M")
                if isinstance(event['end'], datetime):
                    end_time = event['end'].strftime("%-I:%M %p")
                else:
                    end_time = ""
                    
            # Add event to the day
            days_dict[date_key]['events'].append({
                'summary': event['summary'],
                'location': event['location'],
                'start_time': start_time,
                'end_time': end_time,
                'all_day': event['all_day']
            })
        
        # Convert dictionary to sorted list
        list_days = [days_dict[key] for key in sorted(days_dict.keys())]
        
        return {'list_days': list_days}
    
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
            
            return event_list
            
        except Exception as e:
            logger.error(f"Error fetching calendar: {str(e)}")
            return []
    
    def render_error_image(self, dimensions, error_message):
        """Render an error message image."""
        width, height = dimensions
        image = Image.new('RGB', (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        
        try:
            font = get_font('Jost', int(width * 0.05))
            title_font = get_font('Jost-SemiBold', int(width * 0.08))
        except:
            font = get_font('Jost', int(width * 0.05))
            title_font = get_font('Jost', int(width * 0.08))
            
        # Draw error title
        draw.text((width // 2, height // 3), "Calendar Error", 
                 fill=(200, 0, 0), font=title_font, anchor="mm")
        
        # Draw error message with wrapping
        message_lines = self._wrap_text(error_message, font, width * 0.8)
        line_height = font.getbbox("A")[3] + 5
        
        y_position = height // 2
        for line in message_lines:
            draw.text((width // 2, y_position), line, 
                     fill=(0, 0, 0), font=font, anchor="mm")
            y_position += line_height
            
        return image
    
    def _wrap_text(self, text, font, max_width):
        """Wrap text to fit within max_width."""
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            test_line = ' '.join(current_line + [word])
            # getbbox()[2] gives width
            if font.getbbox(test_line)[2] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                    current_line = [word]
                else:
                    # Word is too long, split it
                    current_word = word
                    while font.getbbox(current_word)[2] > max_width:
                        for i in range(len(current_word) - 1, 0, -1):
                            if font.getbbox(current_word[:i])[2] <= max_width:
                                lines.append(current_word[:i] + '-')
                                current_word = current_word[i:]
                                break
                    current_line = [current_word]
        
        if current_line:
            lines.append(' '.join(current_line))
            
        return lines
    
    def render_direct(self, template_path, params, width=800, height=480):
        """Render template directly using PIL."""
        try:
            logging.info("Using direct PIL rendering for iCalendar plugin")
            
            # Create new image - ensure dimensions are integers
            width = int(width)
            height = int(height)
            image = Image.new('RGB', (width, height), (255, 255, 255))
            draw = ImageDraw.Draw(image)
            
            # Define some standard colors
            colors = {
                "text_dark": (50, 50, 50),
                "text_medium": (100, 100, 100),
                "text_light": (150, 150, 150),
                "text_header": (255, 255, 255),
                "primary": (65, 105, 225),  # Royal blue
                "secondary": (245, 245, 255),  # Light blue-gray
                "highlight": (30, 144, 255),  # Dodger blue
                "event_bg": (230, 240, 255),  # Very light blue
                "today_bg": (219, 242, 255),  # Light sky blue
                "grid_lines": (200, 200, 220)  # Light gray
            }
            
            # Load fonts
            try:
                # Try to load Jost-SemiBold first
                header_font = ImageFont.truetype("./src/fonts/Jost-SemiBold.ttf", 26)
                normal_font = ImageFont.truetype("./src/fonts/Jost-Regular.ttf", 18)
                small_font = ImageFont.truetype("./src/fonts/Jost-Regular.ttf", 14)
                logging.info("Successfully loaded Jost fonts for calendar rendering")
            except Exception as e:
                logging.warning(f"Failed to load Jost-SemiBold font: {e}")
                try:
                    # Try to load regular Jost if SemiBold fails
                    header_font = ImageFont.truetype("./src/fonts/Jost-Regular.ttf", 26)
                    normal_font = ImageFont.truetype("./src/fonts/Jost-Regular.ttf", 18)
                    small_font = ImageFont.truetype("./src/fonts/Jost-Regular.ttf", 14)
                    logging.info("Using Jost-Regular fonts for calendar rendering")
                except Exception as e:
                    logging.warning(f"Failed to load Jost fonts: {e}")
                    # Fall back to default font
                    header_font = ImageFont.load_default()
                    normal_font = ImageFont.load_default()
                    small_font = ImageFont.load_default()
                    logging.warning("Using default fonts for calendar rendering")
            
            # Extract template type from path or params
            # First check if viewMode is in settings
            view_mode = params.get('view_mode', '')
            if not view_mode:
                # Try to get from plugin_settings if available
                plugin_settings = params.get('plugin_settings', {})
                view_mode = plugin_settings.get('viewMode', '')
            
            # If still not found, extract from path
            if not view_mode:
                template_name = os.path.basename(template_path).replace('.html', '')
                if 'day' in template_name:
                    view_mode = 'day'
                elif 'week' in template_name:
                    view_mode = 'week'
                else:
                    view_mode = 'list'  # Default to list view
            
            # Draw title bar with calendar title, date and view mode
            title_height = 40
            draw.rectangle([(0, 0), (width, title_height)], fill=colors["primary"])
            
            # Draw title text
            title = self.title if hasattr(self, 'title') else 'Calendar'
            draw.text((10, title_height//2), title, fill=colors["text_header"], font=header_font, anchor="lm")
            
            # Draw date on right side
            today = datetime.now().strftime('%b %d')
            draw.text((width - 10, title_height//2), today, fill=colors["text_header"], font=normal_font, anchor="rm")
            
            try:
                # Render based on view mode
                if view_mode.lower() == 'day':
                    # Draw view mode centered
                    draw.text((width//2, title_height//2), "Day View", fill=colors["text_header"], font=normal_font, anchor="mm")
                    
                    # Prepare parameters for day view rendering
                    if 'day_header' not in params and 'current_date' not in params:
                        current_date = params.get('current_date', datetime.now().strftime('%B %d, %Y'))
                        day_name = params.get('current_day_name', datetime.now().strftime('%A'))
                        params['current_date'] = current_date
                        params['day_name'] = day_name
                    
                    # Format hours for timeline
                    if 'hours' not in params:
                        hours = [f"{h:02d}:00" for h in range(0, 24, 1)]
                        params['hours'] = hours
                        params['current_hour'] = datetime.now().strftime('%H:00')
                    
                    try:
                        # Ensure dimensions are passed as a tuple of integers
                        self._render_direct_day_view(draw, params, (int(width), int(height)), colors, header_font, normal_font, small_font)
                        logging.info("Day view rendering completed successfully")
                    except Exception as e:
                        logging.error(f"Error rendering day view: {str(e)}")
                        self._render_error_message(draw, width, height, f"Error rendering day view: {str(e)}", header_font)
                        # Fall back to list view
                        self._render_direct_list_view(draw, params, (int(width), int(height)), colors, header_font, normal_font, small_font)
                
                elif view_mode.lower() == 'week':
                    # Draw view mode centered
                    draw.text((width//2, title_height//2), "Week View", fill=colors["text_header"], font=normal_font, anchor="mm")
                    
                    # Ensure all required parameters are present
                    if 'calendar_grid' not in params or not params['calendar_grid']:
                        # Create a basic grid structure if none exists
                        calendar_grid = []
                        for _ in range(5):  # 5 weeks
                            week = []
                            for j in range(7):  # 7 days
                                week.append({'day': j+1, 'different_month': False, 'has_events': False})
                            calendar_grid.append(week)
                        params['calendar_grid'] = calendar_grid
                    
                    if 'upcoming_events' not in params:
                        params['upcoming_events'] = []
                    
                    if 'month_name' not in params:
                        params['month_name'] = datetime.now().strftime('%B %Y')
                    
                    try:
                        # Render week view with error handling - ensure dimensions are passed correctly
                        self._render_direct_week_view(draw, params, (int(width), int(height)), colors, header_font, normal_font, small_font)
                        logging.info("Week view rendering completed successfully")
                    except Exception as e:
                        logging.error(f"Error rendering week view: {str(e)}")
                        self._render_error_message(draw, width, height, f"Error rendering week view: {str(e)}", header_font)
                        # Fall back to list view
                        self._render_direct_list_view(draw, params, (int(width), int(height)), colors, header_font, normal_font, small_font)
                
                else:  # Default to list view
                    # Draw view mode centered
                    draw.text((width//2, title_height//2), "List View", fill=colors["text_header"], font=normal_font, anchor="mm")
                    
                    # Format days for list view
                    days = params.get('days', [])
                    if not days and 'list_days' in params:
                        # Convert from old format if needed
                        days = []
                        for day_section in params.get('list_days', []):
                            events = []
                            for event in day_section.get('events', []):
                                events.append({
                                    'summary': event.get('summary', ''),
                                    'location': event.get('location', ''),
                                    'time': event.get('time', ''),
                                    'all_day': event.get('all_day', False)
                                })
                            
                            days.append({
                                'date': day_section.get('date', ''),
                                'day_name': day_section.get('name', ''),
                                'events': events,
                                'is_today': day_section.get('is_today', False)
                            })
                        
                        params['days'] = days
                    
                    try:
                        # Render list view with error handling - ensure dimensions are integers
                        self._render_direct_list_view(draw, params, (int(width), int(height)), colors, header_font, normal_font, small_font)
                        logging.info("List view rendering completed successfully")
                    except Exception as e:
                        logging.error(f"Error rendering list view: {str(e)}")
                        self._render_error_message(draw, width, height, f"Error rendering list view: {str(e)}", header_font)
            
            except Exception as e:
                logging.error(f"Error in view-specific rendering: {str(e)}")
                self._render_error_message(draw, width, height, f"Error in view rendering: {str(e)}", header_font)
            
            # Save and return the image
            if template_path.endswith('.html'):
                img_path = template_path.replace('.html', '.png')
            else:
                img_path = f"{template_path}.png"
            
            image.save(img_path)
            logging.info(f"Direct PIL rendering complete: {img_path}")
            return image
            
        except Exception as e:
            logging.error(f"Error in direct PIL rendering: {str(e)}")
            # Create a simple error image
            error_img = Image.new('RGB', (width, height), color=(255, 255, 255))
            draw = ImageDraw.Draw(error_img)
            try:
                font = ImageFont.load_default()
                draw.text((width//2, height//2), f"Error: {str(e)}", fill=(0, 0, 0), font=font, anchor="mm")
            except:
                pass
            return error_img
            
    def _render_error_message(self, draw, width, height, message, font=None):
        """Render an error message on the image."""
        try:
            # Use default font if none provided
            if font is None:
                font = ImageFont.load_default()
                
            # Draw error background
            draw.rectangle([(width * 0.1, height * 0.4), (width * 0.9, height * 0.6)], fill=(255, 240, 240), outline=(255, 0, 0))
            
            # Draw error message
            # Split message into multiple lines if too long
            if len(message) > 50:
                words = message.split()
                lines = []
                current_line = ""
                
                for word in words:
                    if len(current_line + " " + word) <= 50:
                        current_line += (" " + word if current_line else word)
                    else:
                        lines.append(current_line)
                        current_line = word
                
                if current_line:
                    lines.append(current_line)
                
                # Draw each line
                for i, line in enumerate(lines):
                    y_pos = height * 0.45 + i * 30
                    draw.text((width//2, y_pos), line, fill=(200, 0, 0), font=font, anchor="mm")
            else:
                # Draw single line message
                draw.text((width//2, height//2), message, fill=(200, 0, 0), font=font, anchor="mm")
        except Exception as e:
            logging.error(f"Error in rendering error message: {str(e)}")
            # Last resort if even error rendering fails
            try:
                draw.rectangle([(0, 0), (width, height)], fill=(255, 200, 200))
                draw.text((width//2, height//2), "Rendering Error", fill=(0, 0, 0), font=ImageFont.load_default(), anchor="mm")
            except:
                pass

    def display(self, config):
        """Generate a calendar image."""
        logger.info("Generating calendar image...")
        
        # Get plugin settings
        settings = config.get('plugin_settings', {})
        
        try:
            # Prepare output file path
            output_path = resolve_path(settings.get('output_path', 'calendar.png'))
            logger.info(f"Output path: {output_path}")
            
            # Get calendar URL from settings
            calendar_url = settings.get('calendar_url', '')
            if not calendar_url:
                logger.error("No calendar URL provided")
                return self.render_error_image((800, 480), "No calendar URL provided")
            
            # Determine view mode
            view_mode = settings.get('view_mode', 'day')
            logger.info(f"Calendar view mode: {view_mode}")
            
            # Get width and height from layout settings
            width = config.get('width', 800)
            height = config.get('height', 480)
            dimensions = (width, height)
            
            # Parse iCalendar data
            params = self.fetch_and_parse_ics(calendar_url, view_mode)
            if not params:
                logger.error("Failed to parse iCalendar data")
                return self.render_error_image(dimensions, "Failed to parse iCalendar data")
            
            # Add plugin settings to params for use in rendering
            params['plugin_settings'] = settings
            params['title'] = settings.get('name', 'Calendar')
            params['view_mode'] = view_mode
            
            # Check if chromium-browser is available
            try:
                # Test if chromium-browser is installed by trying to get its version
                subprocess.run(['chromium-browser', '--version'], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.PIPE, 
                              check=True)
                
                # If we got here, chromium is available
                logger.info("chromium-browser is available, using HTML rendering")
                return self.render_html(output_path, params, dimensions)
            except (subprocess.SubprocessError, FileNotFoundError):
                # If chromium-browser is not available, use direct rendering
                logger.info("chromium-browser not found, using fallback rendering method")
                return self.render_direct(output_path, params, dimensions)
                
        except Exception as e:
            logger.error(f"Error in calendar display: {str(e)}")
            return self.render_error_image((800, 480), str(e))

    def render_html(self, img_path, params, dimensions=(800, 480)):
        """Render calendar using HTML templates and chromium-browser."""
        try:
            # Extract width and height from dimensions
            if isinstance(dimensions, tuple) and len(dimensions) == 2:
                width, height = int(dimensions[0]), int(dimensions[1])
            else:
                # Default values if dimensions are invalid
                width, height = 800, 480
                logger.warning(f"Invalid dimensions format: {dimensions}, using defaults")
                
            # Get paths to templates
            html_file = resolve_path("src/plugins/icalendar/templates/calendar.html")
            css_file = resolve_path("src/plugins/icalendar/templates/styles.css")
            
            # Check if the template files exist
            if not os.path.exists(html_file):
                html_file = os.path.join(os.path.dirname(__file__), "templates/calendar.html")
                if not os.path.exists(html_file):
                    logger.error(f"HTML template file not found: {html_file}")
                    return self.render_direct(img_path, params, width, height)
            
            if not os.path.exists(css_file):
                css_file = os.path.join(os.path.dirname(__file__), "templates/styles.css")
                if not os.path.exists(css_file):
                    logger.error(f"CSS template file not found: {css_file}")
                    return self.render_direct(img_path, params, width, height)

            # Create a temporary HTML file
            with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
                temp_html_path = f.name

            # Get color scheme
            settings = params.get('plugin_settings', {})
            color_scheme = settings.get('colorScheme', 'blue')

            # Generate HTML content
            view_mode = params.get('view_mode', 'day')
            template_params = {
                'params': params,
                'color_scheme': color_scheme,
                'view_mode': view_mode
            }

            # Read template files
            try:
                with open(html_file, 'r') as f:
                    html_template = f.read()
                with open(css_file, 'r') as f:
                    css_template = f.read()
            except FileNotFoundError as e:
                logger.error(f"Template file not found: {str(e)}")
                # Fall back to direct rendering
                return self.render_direct(img_path, params, width, height)

            # Apply parameters to template
            html_content = self._apply_template(html_template, template_params)

            # Write HTML to temporary file
            with open(temp_html_path, 'w') as f:
                f.write(html_content)

            # Use chromium-browser to render HTML to PNG
            browser_command = [
                'chromium-browser',
                '--headless',
                '--disable-gpu',
                f'--window-size={width},{height}',
                f'--screenshot={img_path}',
                f'file://{temp_html_path}'
            ]

            logger.info(f"Running browser command: {' '.join(browser_command)}")
            process = subprocess.run(browser_command, capture_output=True)

            # Clean up temporary file
            os.unlink(temp_html_path)

            if process.returncode != 0:
                logger.error(f"Browser process failed with code {process.returncode}")
                logger.error(f"Error output: {process.stderr.decode()}")
                # Fall back to direct rendering
                return self.render_direct(img_path, params, width, height)

            logger.info(f"HTML rendering complete: {img_path}")
            
            # Load the image and return it
            try:
                img = Image.open(img_path)
                return img
            except Exception as e:
                logger.error(f"Failed to load rendered image: {str(e)}")
                # Fall back to direct rendering
                return self.render_direct(img_path, params, width, height)

        except Exception as e:
            logger.error(f"Error in HTML rendering: {str(e)}")
            # Fall back to direct rendering
            if isinstance(dimensions, tuple) and len(dimensions) == 2:
                width, height = int(dimensions[0]), int(dimensions[1])
            else:
                width, height = 800, 480
            return self.render_direct(img_path, params, width, height)

    def _apply_template(self, template, params):
        """Simple template engine to replace parameters in HTML template."""
        # Replace params in template
        params_dict = params.get('params', {})
        
        # Add special parameters for view mode
        view_mode = params.get('view_mode', 'day')
        color_scheme = params.get('color_scheme', 'blue')
        
        # Create specialized templates based on view mode
        if view_mode == 'day':
            template = template.replace('{{VIEW_CONTENT}}', self._get_day_view_html(params_dict))
        elif view_mode == 'week':
            template = template.replace('{{VIEW_CONTENT}}', self._get_week_view_html(params_dict))
        else:  # Default to list view
            template = template.replace('{{VIEW_CONTENT}}', self._get_list_view_html(params_dict))
            
        # Replace color scheme class
        template = template.replace('{{COLOR_SCHEME}}', color_scheme)
        
        # Replace title
        title = params_dict.get('title', 'Calendar')
        template = template.replace('{{TITLE}}', title)
        
        return template
        
    def _get_day_view_html(self, params):
        """Generate HTML content for day view."""
        # Simplified placeholder implementation
        events = params.get('events', [])
        all_day_events = [e for e in events if e.get('all_day')]
        timed_events = [e for e in events if not e.get('all_day')]
        
        html = '<div class="day-view">'
        html += f'<h2 class="date">{params.get("date", "")}</h2>'
        
        # All-day events section
        html += '<div class="all-day-events">'
        if all_day_events:
            for event in all_day_events:
                html += f'<div class="event all-day"><div class="event-title">{event.get("summary", "")}</div>'
                if event.get('location'):
                    html += f'<div class="event-location">{event.get("location")}</div>'
                html += '</div>'
        html += '</div>'
        
        # Hourly timeline for timed events
        html += '<div class="timeline">'
        for hour in range(0, 24):
            ampm = 'AM' if hour < 12 else 'PM'
            display_hour = hour if hour <= 12 else hour - 12
            if display_hour == 0:
                display_hour = 12
                
            html += f'<div class="hour"><div class="hour-label">{display_hour} {ampm}</div><div class="hour-events">'
            
            # Add events for this hour
            hour_events = [e for e in timed_events if self._event_in_hour(e, hour)]
            for event in hour_events:
                html += f'<div class="event"><div class="event-time">{event.get("time", "")}</div>'
                html += f'<div class="event-title">{event.get("summary", "")}</div>'
                if event.get('location'):
                    html += f'<div class="event-location">{event.get("location")}</div>'
                html += '</div>'
                
            html += '</div></div>'  # Close hour-events and hour
            
        html += '</div>'  # Close timeline
        html += '</div>'  # Close day-view
        
        return html
        
    def _event_in_hour(self, event, hour):
        """Check if an event falls within a specific hour."""
        event_time = event.get('time', '')
        if not event_time:
            return False
            
        try:
            # Simple check for now - just look for the hour in the event time string
            event_hour = int(event_time.split(':')[0])
            return event_hour == hour
        except (ValueError, IndexError):
            return False
            
    def _get_week_view_html(self, params):
        """Generate HTML content for week view."""
        # Simplified placeholder implementation
        calendar_grid = params.get('calendar_grid', [])
        upcoming_events = params.get('upcoming_events', [])
        
        html = '<div class="week-view">'
        html += f'<h2>{params.get("month_name", "")}</h2>'
        
        # Calendar grid
        html += '<div class="calendar-grid">'
        html += '<div class="weekdays">'
        weekdays = params.get('weekdays', ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
        for day in weekdays[:7]:
            html += f'<div class="weekday">{day}</div>'
        html += '</div>'
        
        # Calendar days
        html += '<div class="days">'
        for week in calendar_grid[:5]:  # Limit to 5 weeks
            html += '<div class="week">'
            for day in week[:7]:  # Limit to 7 days
                classes = ['day']
                if day.get('different_month'):
                    classes.append('different-month')
                if day.get('today'):
                    classes.append('today')
                if day.get('has_events'):
                    classes.append('has-events')
                    
                html += f'<div class="{" ".join(classes)}">{day.get("day", "")}</div>'
            html += '</div>'  # Close week
        html += '</div>'  # Close days
        html += '</div>'  # Close calendar-grid
        
        # Upcoming events
        html += '<div class="upcoming-events">'
        html += '<h3>Coming Up</h3>'
        html += '<div class="events-list">'
        for event in upcoming_events[:5]:  # Limit to 5 events
            html += f'<div class="event"><div class="event-day">{event.get("day", "")}</div>'
            html += f'<div class="event-details"><div class="event-time">'
            html += 'All Day' if event.get('all_day') else event.get('time', '')
            html += f'</div><div class="event-title">{event.get("summary", "")}</div></div></div>'
        html += '</div>'  # Close events-list
        html += '</div>'  # Close upcoming-events
        
        html += '</div>'  # Close week-view
        
        return html
        
    def _get_list_view_html(self, params):
        """Generate HTML content for list view."""
        # Simplified placeholder implementation
        days = params.get('days', [])
        
        html = '<div class="list-view">'
        
        for day in days:
            day_date = day.get('date', '')
            day_name = day.get('day_name', '')
            events = day.get('events', [])
            
            if not events:
                continue
                
            html += f'<div class="day-section"><div class="day-header">'
            html += f'<div class="day-name">{day_name}</div>'
            html += f'<div class="day-date">{day_date}</div>'
            html += '</div>'  # Close day-header
            
            html += '<div class="day-events">'
            for event in events:
                html += '<div class="event">'
                html += f'<div class="event-time">'
                html += 'All Day' if event.get('all_day') else event.get('time', '')
                html += '</div>'
                html += f'<div class="event-title">{event.get("summary", "")}</div>'
                if event.get('location'):
                    html += f'<div class="event-location">{event.get("location")}</div>'
                html += '</div>'  # Close event
            html += '</div>'  # Close day-events
            html += '</div>'  # Close day-section
            
        html += '</div>'  # Close list-view
        
        return html 

    def fetch_and_parse_ics(self, calendar_url, view_mode):
        """Fetch and parse iCalendar data for the given view mode."""
        try:
            # Get current time for timezone calculations
            now = datetime.now(pytz.timezone(DEFAULT_TIMEZONE))
            
            # Fetch events
            events = self.fetch_calendar_events(calendar_url, now, DEFAULT_DAYS_TO_SHOW, DEFAULT_MAX_EVENTS, now.tzinfo)
            
            # Prepare data based on view mode
            params = {}
            if view_mode == "day":
                params = self.prepare_day_view_data(events, now, now.tzinfo)
            elif view_mode == "week":
                params = self.prepare_week_view_data(events, now, now.tzinfo)
            else:  # list view
                params = self.prepare_list_view_data(events, now, now.tzinfo)
                
            # Add common params
            params['current_date'] = now.strftime("%A, %B %d")
            
            return params
        except Exception as e:
            logger.error(f"Error fetching and parsing iCal data: {e}")
            return None 

    def _draw_rounded_rectangle(self, draw, xy, fill=None, outline=None, width=1, radius=10):
        """Draw a rectangle with rounded corners."""
        try:
            # Extract coordinates, ensuring they are floats
            if isinstance(xy, (list, tuple)):
                if len(xy) == 2 and all(isinstance(point, (list, tuple)) for point in xy):
                    # Format is [(x0, y0), (x1, y1)]
                    (x0, y0), (x1, y1) = xy
                elif len(xy) == 4:
                    # Format is [x0, y0, x1, y1]
                    x0, y0, x1, y1 = xy
                else:
                    logger.error(f"Invalid coordinate format for _draw_rounded_rectangle: {xy}")
                    draw.rectangle([(0, 0), (10, 10)], fill=(255, 0, 0))  # Error marker
                    return
            else:
                logger.error(f"Invalid type for xy in _draw_rounded_rectangle: {type(xy)}")
                draw.rectangle([(0, 0), (10, 10)], fill=(255, 0, 0))  # Error marker
                return

            # Convert all coordinates to floats
            x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)

            # Ensure x0 <= x1 and y0 <= y1
            if x0 > x1:
                x0, x1 = x1, x0
            if y0 > y1:
                y0, y1 = y1, y0

            # Draw the rectangle without rounded corners for now
            # This is a safe fallback that should work in all cases
            draw.rectangle([(x0, y0), (x1, y1)], fill=fill, outline=outline, width=width)

        except Exception as e:
            logger.error(f"Error in _draw_rounded_rectangle: {str(e)}")
            # Draw a simple error indicator
            try:
                draw.rectangle([(0, 0), (10, 10)], fill=(255, 0, 0))  # Error marker
            except:
                pass

    def _render_direct_day_view(self, draw, params, dimensions, colors, header_font, normal_font, small_font):
        """Render day view directly using PIL."""
        try:
            # Ensure dimensions are properly formatted as floats
            if isinstance(dimensions, tuple) and len(dimensions) == 2:
                width, height = float(dimensions[0]), float(dimensions[1])
            else:
                # In case of invalid dimensions, use defaults
                width, height = 800.0, 480.0
                logger.warning(f"Invalid dimensions in _render_direct_day_view: {dimensions}, using defaults")
            
            # Create a vibrant color palette
            vibrant_colors = {
                "primary": (65, 105, 225),      # Royal Blue
                "secondary": (240, 248, 255),   # Alice Blue
                "highlight": (30, 144, 255),    # Dodger Blue
                "today_bg": (176, 224, 230),    # Powder Blue
                "event_primary": (70, 130, 180), # Steel Blue
                "event_secondary": (135, 206, 250), # Light Sky Blue
                "event_all_day": (106, 90, 205), # Slate Blue
                "timeline": (176, 196, 222),     # Light Steel Blue
                "current_hour": (255, 99, 71)    # Tomato
            }
            
            # Draw the date header with gradient background
            header_height = float(height * 0.15)
            
            # Draw header background with gradient
            self._draw_rounded_rectangle(
                draw,
                [(float(width * 0.05), float(10)), (float(width * 0.95), float(header_height))],
                fill=vibrant_colors["primary"],
                radius=15
            )
            
            # Current date info
            date_str = params.get('current_date', '')
            day_name = params.get('day_name', '')
            
            # Draw day name (large)
            draw.text(
                (float(width * 0.5), float(header_height * 0.4)),
                day_name,
                fill=(255, 255, 255),
                font=header_font,
                anchor="mm"
            )
            
            # Draw date (smaller)
            draw.text(
                (float(width * 0.5), float(header_height * 0.75)),
                date_str,
                fill=(255, 255, 255),
                font=normal_font,
                anchor="mm"
            )
            
            # Draw timeline area
            timeline_start_y = float(header_height + 10)
            timeline_end_y = float(height - 20)
            timeline_height = float(timeline_end_y - timeline_start_y)
            
            # Draw background for timeline
            draw.rectangle(
                [(float(width * 0.05), float(timeline_start_y)), (float(width * 0.95), float(timeline_end_y))],
                fill=vibrant_colors["secondary"],
                outline=None
            )
            
            # Draw timeline
            hours = params.get('hours', [])
            hour_height = float(timeline_height / len(hours))
            timeline_x = float(width * 0.15)
            
            # Get current hour
            current_hour = params.get('current_hour', None)
            
            # Draw hours timeline
            for idx, hour in enumerate(hours):
                hour_y = float(timeline_start_y + (idx * hour_height))
                
                # Draw hour text
                is_current = hour == current_hour
                hour_color = vibrant_colors["current_hour"] if is_current else vibrant_colors["timeline"]
                
                # Draw hour marker (circle for current hour, line for others)
                if is_current:
                    # Circle for current hour
                    circle_radius = float(12)
                    draw.ellipse(
                        [(float(timeline_x - circle_radius), float(hour_y - circle_radius)),
                         (float(timeline_x + circle_radius), float(hour_y + circle_radius))],
                        fill=hour_color
                    )
                    draw.text(
                        (float(timeline_x), float(hour_y)),
                        hour,
                        fill=(255, 255, 255),
                        font=small_font,
                        anchor="mm"
                    )
                else:
                    # Line for other hours
                    draw.line(
                        [(float(width * 0.05), float(hour_y)), (float(width * 0.95), float(hour_y))],
                        fill=hour_color,
                        width=1
                    )
                    draw.text(
                        (float(timeline_x - 15), float(hour_y)),
                        hour,
                        fill=colors["text_dark"],
                        font=small_font,
                        anchor="rm"
                    )
            
            # Draw all-day events at the top
            all_day_events = params.get('all_day_events', [])
            if all_day_events:
                all_day_header_y = float(header_height + 20)
                
                # Draw "All Day" header
                draw.text(
                    (float(width * 0.5), float(all_day_header_y)),
                    "ALL DAY EVENTS",
                    fill=vibrant_colors["event_all_day"],
                    font=normal_font,
                    anchor="mm"
                )
                
                # Draw all day events
                for idx, event in enumerate(all_day_events[:2]):  # Limit to 2 all-day events
                    event_y = float(all_day_header_y + 25 + (idx * 40))
                    
                    # Draw rounded event card
                    self._draw_rounded_rectangle(
                        draw,
                        [(float(width * 0.2), float(event_y)), (float(width * 0.8), float(event_y + 30))],
                        fill=vibrant_colors["event_all_day"],
                        radius=10
                    )
                    
                    # Draw event title
                    summary = event.get('summary', '')
                    if len(summary) > 30:
                        summary = summary[:27] + "..."
                    
                    draw.text(
                        (float(width * 0.5), float(event_y + 15)),
                        summary,
                        fill=(255, 255, 255),
                        font=normal_font,
                        anchor="mm"
                    )
            
            # Draw regular events
            events = params.get('events', [])
            for event in events:
                # Get event details
                start_y = event.get('start_y')
                end_y = event.get('end_y')
                summary = event.get('summary', '')
                location = event.get('location', '')
                time_str = event.get('time', '')
                
                if start_y is None or end_y is None:
                    continue
                    
                # Adjust to our coordinate system
                start_y = float(timeline_start_y + (float(start_y) * timeline_height))
                end_y = float(timeline_start_y + (float(end_y) * timeline_height))
                
                # Ensure minimum height for visibility
                if end_y - start_y < 40:
                    avg_y = (start_y + end_y) / 2
                    start_y = float(avg_y - 20)
                    end_y = float(avg_y + 20)
                
                # Draw event card with rounded corners and gradient
                event_left = float(timeline_x + 20)
                event_right = float(width * 0.9)
                
                # Draw the event rectangle with rounded corners
                self._draw_rounded_rectangle(
                    draw,
                    [(float(event_left), float(start_y)), (float(event_right), float(end_y))],
                    fill=vibrant_colors["event_primary"],
                    radius=8
                )
                
                # Calculate available space for text
                available_height = float(end_y - start_y)
                
                # Only show time if there's enough space
                line_spacing = float(min(20, available_height / 3))
                text_start_y = float(start_y + 8)
                
                # Draw time
                draw.text(
                    (float(event_left + 10), float(text_start_y)),
                    time_str,
                    fill=(255, 255, 255),
                    font=small_font
                )
                
                # Draw summary
                if len(summary) > 25:
                    summary = summary[:22] + "..."
                    
                draw.text(
                    (float(event_left + 10), float(text_start_y + line_spacing)),
                    summary,
                    fill=(255, 255, 255),
                    font=normal_font
                )
                
                # Draw location if available and there's enough space
                if location and available_height > 50:
                    if len(location) > 30:
                        location = location[:27] + "..."
                        
                    draw.text(
                        (float(event_left + 10), float(text_start_y + 2 * line_spacing)),
                        location,
                        fill=(220, 220, 220),
                        font=small_font
                    )
        except Exception as e:
            # Log the specific error and draw error indicator
            logging.error(f"Error in _render_direct_day_view: {str(e)}, {type(e)}")
            # Draw error indicator
            try:
                draw.rectangle([(0, 0), (100, 30)], fill=(255, 0, 0))
                draw.text((50, 15), "RENDER ERROR", fill=(255, 255, 255), font=small_font, anchor="mm")
            except:
                pass
            # Re-raise to be caught by caller
            raise

    def _render_direct_week_view(self, draw, params, dimensions, colors, header_font, normal_font, small_font):
        """Render week view directly using PIL."""
        try:
            # Ensure dimensions are properly formatted as floats
            if isinstance(dimensions, tuple) and len(dimensions) == 2:
                width, height = float(dimensions[0]), float(dimensions[1])
            else:
                # In case of invalid dimensions, use defaults
                width, height = 800.0, 480.0
                logger.warning(f"Invalid dimensions in _render_direct_week_view: {dimensions}, using defaults")
            
            # Create a more vibrant color palette
            vibrant_colors = {
                "primary": (65, 105, 225),      # Royal Blue
                "secondary": (240, 248, 255),   # Alice Blue
                "highlight": (30, 144, 255),    # Dodger Blue
                "today_bg": (176, 224, 230),    # Powder Blue
                "weekend": (70, 130, 180),      # Steel Blue
                "event_dot": (255, 99, 71),     # Tomato
                "section_bg": (245, 245, 250)   # Off-white with blue tint
            }
            
            # Draw month heading with background and rounded corners
            month_title_y = float(height * 0.2)
            title_height = float(45)
            title_width = float(width * 0.8)
            title_x = float(width * 0.1)
            
            # Draw a background for the month heading
            # Safely create coordinates for rectangle
            x1 = float(title_x)
            y1 = float(month_title_y - title_height/2)
            x2 = float(title_x + title_width)
            y2 = float(month_title_y + title_height/2)
            
            self._draw_rounded_rectangle(
                draw, 
                [(x1, y1), (x2, y2)],
                fill=vibrant_colors["primary"],
                radius=15
            )
            
            # Draw month name with shadow for better visibility
            # Draw shadow first
            shadow_offset = 1
            draw.text((float(width/2 + shadow_offset), float(month_title_y + shadow_offset)), 
                     params.get('month_name', 'Calendar'), 
                     fill=(45, 85, 205), font=header_font, anchor="mm")
            
            # Draw actual text on top
            draw.text((float(width/2), float(month_title_y)), 
                     params.get('month_name', 'Calendar'), 
                     fill=(255, 255, 255), font=header_font, anchor="mm")
            
            # Draw weekday headers with gradient background
            weekdays = params.get('weekdays', ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"])
            if not weekdays or len(weekdays) == 0:
                weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                
            day_width = float(width / 7)
            
            weekday_y = float(height * 0.25)
            # Draw gradient background for weekday header
            for i in range(7):
                x_start = float(i * day_width)
                # Use different color for weekend days
                if i == 0 or i == 6:  # Sunday or Saturday
                    header_color = vibrant_colors["weekend"]
                else:
                    header_color = vibrant_colors["primary"]
                
                # Explicitly define rectangle coordinates as floats
                x_end = float(x_start + day_width)
                y_start = float(weekday_y - 15)
                y_end = float(weekday_y + 15)
                
                draw.rectangle(
                    [(x_start, y_start), (x_end, y_end)],
                    fill=header_color,
                    outline=None
                )
            
            # Draw the weekday names
            for i, day in enumerate(weekdays[:7]):  # Limit to 7 days
                x = float(i * day_width + day_width/2)
                draw.text((x, weekday_y), 
                         day, 
                         fill=(255, 255, 255), font=small_font, anchor="mm")
            
            # Draw calendar grid with better styling
            calendar_grid = params.get('calendar_grid', [])
            if not calendar_grid:
                # Create an empty grid if none provided
                calendar_grid = []
                for _ in range(5):  # 5 weeks
                    week = []
                    for j in range(7):  # 7 days
                        week.append({'day': j+1, 'different_month': False, 'has_events': False})
                    calendar_grid.append(week)
            
            grid_top = float(height * 0.3)
            grid_height = float(height * 0.3)
            
            # Draw grid background with slight blue tint
            # Safe coordinate definition
            x_start, y_start = float(0), float(grid_top)
            x_end, y_end = float(width), float(grid_top + grid_height)
            
            draw.rectangle(
                [(x_start, y_start), (x_end, y_end)],
                fill=vibrant_colors["section_bg"],
                outline=colors["grid_lines"]
            )
            
            # Maximum of 5 rows for weeks
            max_weeks = min(len(calendar_grid), 5) if calendar_grid else 5
            row_height = float(grid_height / max_weeks)
            
            # Draw grid lines
            for i in range(1, 7):  # Vertical lines between days
                x = float(i * day_width)
                draw.line([(x, grid_top), (x, grid_top + grid_height)], fill=colors["grid_lines"], width=1)
                
            for i in range(1, max_weeks):  # Horizontal lines between weeks
                y = float(grid_top + i * row_height)
                draw.line([(0, y), (width, y)], fill=colors["grid_lines"], width=1)
            
            # Draw days and event indicators
            if calendar_grid:
                for i, week in enumerate(calendar_grid[:max_weeks]):
                    for j, day in enumerate(week[:7]):  # Limit to 7 days per week
                        x = float(j * day_width + day_width/2)
                        y = float(grid_top + i * row_height + row_height/2)
                        
                        # Default color for days
                        fill_color = colors["text_dark"]
                        
                        # Different styling for weekend days
                        if j == 0 or j == 6:  # Sunday or Saturday
                            if not day.get('different_month'):
                                fill_color = vibrant_colors["weekend"]
                        
                        # Different color for days not in current month
                        if day.get('different_month'):
                            fill_color = colors["text_light"]
                        
                        # Special highlight for today
                        if day.get('today'):
                            # Draw a filled circle with rounded border for today
                            circle_radius = float(min(day_width, row_height) / 3.5)
                            
                            # Safe coordinate definition for ellipse
                            ellipse_x1 = float(x - circle_radius)
                            ellipse_y1 = float(y - circle_radius)
                            ellipse_x2 = float(x + circle_radius)
                            ellipse_y2 = float(y + circle_radius)
                            
                            # Draw filled circle for today's background
                            draw.ellipse(
                                [(ellipse_x1, ellipse_y1), (ellipse_x2, ellipse_y2)],
                                fill=vibrant_colors["today_bg"],
                                outline=vibrant_colors["primary"]
                            )
                            fill_color = vibrant_colors["primary"]  # Darker text for contrast
                        
                        # Draw the day number
                        day_text = str(day.get('day', ''))
                        draw.text((x, y), day_text, fill=fill_color, font=normal_font, anchor="mm")
                        
                        # Draw a colorful event indicator dot if there are events
                        if day.get('has_events'):
                            dot_y = float(y + row_height/4)
                            # Safe coordinate definition for ellipse
                            dot_x1 = float(x - 4)
                            dot_y1 = float(dot_y)
                            dot_x2 = float(x + 4)
                            dot_y2 = float(dot_y + 8)
                            
                            draw.ellipse(
                                [(dot_x1, dot_y1), (dot_x2, dot_y2)],
                                fill=vibrant_colors["event_dot"]
                            )
            
            # Draw upcoming events section with enhanced styling
            upcoming_top = float(grid_top + grid_height + 20)
            upcoming_title_height = float(35)
            
            # Draw section header with rounded corners
            # Safe coordinate definition
            header_x1 = float(width * 0.1)
            header_y1 = float(upcoming_top)
            header_x2 = float(width * 0.9)
            header_y2 = float(upcoming_top + upcoming_title_height)
            
            self._draw_rounded_rectangle(
                draw,
                [(header_x1, header_y1), (header_x2, header_y2)],
                fill=vibrant_colors["primary"],
                radius=12
            )
            
            draw.text(
                (float(width/2), float(upcoming_top + upcoming_title_height/2)), 
                "Coming Up", 
                fill=(255, 255, 255), font=header_font, anchor="mm"
            )
            
            # Draw event list with better styling
            event_top = float(upcoming_top + upcoming_title_height + 10)
            event_height = float(40)
            # Safely get upcoming events, default to empty list
            upcoming_events = params.get('upcoming_events', [])
            max_events = min(len(upcoming_events), 5)
            
            if max_events == 0:
                # Draw a "No upcoming events" message
                draw.text(
                    (float(width/2), float(event_top + 25)),
                    "No upcoming events",
                    fill=colors["text_medium"], font=normal_font, anchor="mm"
                )
            else:
                for i, event in enumerate(upcoming_events[:max_events]):
                    current_y = float(event_top + i * event_height)
                    
                    # Draw event background with rounded corners
                    # Safe coordinate definition
                    event_x1 = float(width * 0.05)
                    event_y1 = float(current_y)
                    event_x2 = float(width * 0.95)
                    event_y2 = float(current_y + event_height - 5)
                    
                    self._draw_rounded_rectangle(
                        draw,
                        [(event_x1, event_y1), (event_x2, event_y2)],
                        fill=colors["event_bg"],
                        radius=10
                    )
                    
                    # Draw colored left border
                    # Safe coordinate definition
                    border_x1 = float(width * 0.05)
                    border_y1 = float(current_y + 2)
                    border_x2 = float(width * 0.05 + 5)
                    border_y2 = float(current_y + event_height - 7)
                    
                    draw.rectangle(
                        [(border_x1, border_y1), (border_x2, border_y2)],
                        fill=vibrant_colors["primary"],
                        outline=None
                    )
                    
                    # Draw day indicator
                    day_width = float(width * 0.12)
                    draw.text(
                        (float(width * 0.05 + day_width/2), float(current_y + event_height/2 - 2)),
                        event.get('day', ''),
                        fill=vibrant_colors["primary"], font=small_font, anchor="mm"
                    )
                    
                    # Format time text
                    time_text = "All Day" if event.get('all_day') else event.get('time', '')
                    
                    # Draw event details
                    details_x = float(width * 0.05 + day_width + 10)
                    draw.text(
                        (details_x, float(current_y + 9)),
                        time_text,
                        fill=colors["text_light"], font=small_font
                    )
                    
                    # Truncate summary if too long
                    summary = event.get('summary', '')
                    if len(summary) > 25:
                        summary = summary[:22] + "..."
                        
                    draw.text(
                        (details_x, float(current_y + 25)),
                        summary,
                        fill=colors["text_dark"], font=small_font
                    )
        except Exception as e:
            # Log the specific error and draw error indicator
            logging.error(f"Error in _render_direct_week_view: {str(e)}, {type(e)}")
            # Draw error indicator
            try:
                draw.rectangle([(0, 0), (100, 30)], fill=(255, 0, 0))
                draw.text((50, 15), "RENDER ERROR", fill=(255, 255, 255), font=small_font, anchor="mm")
            except:
                pass
            # Re-raise to be caught by caller
            raise

    def _render_direct_list_view(self, draw, params, dimensions, colors, header_font, normal_font, small_font):
        """Render list view directly using PIL."""
        try:
            width, height = float(dimensions[0]), float(dimensions[1])
            
            # Create a vibrant color palette
            vibrant_colors = {
                "primary": (65, 105, 225),      # Royal Blue
                "secondary": (240, 248, 255),   # Alice Blue
                "highlight": (30, 144, 255),    # Dodger Blue
                "today_bg": (176, 224, 230),    # Powder Blue
                "weekend": (70, 130, 180),      # Steel Blue
                "event_dot": (255, 99, 71),     # Tomato
                "day_header": (100, 149, 237),  # Cornflower Blue
                "gradient_end": (135, 206, 250) # Light Sky Blue
            }
            
            # Calculate available height for days
            days = params.get('days', [])
            y_position = float(height * 0.15)  # Start below the title
            max_height = float(height - y_position - 20)  # Leave some margin at bottom
            
            # If we have no days to display, show a message
            if not days:
                draw.text((width/2, height/2), 
                        "No events to display", 
                        fill=colors["text_medium"], font=normal_font, anchor="mm")
                return
                
            # Sort days by date
            days.sort(key=lambda x: x.get('date_obj', None) if x.get('date_obj') else 0)
            
            # Calculate how many days we can fit
            day_section_height = float(max_height / min(len(days), 5))  # Show up to 5 days max
            
            # Draw gradient background for entire list area
            bg_coords = [
                (float(width * 0.05), float(y_position)),
                (float(width * 0.95), float(y_position + max_height))
            ]
            
            draw.rectangle(bg_coords, fill=vibrant_colors["secondary"], outline=None)
            
            # Draw each day section
            for day_idx, day in enumerate(days[:5]):  # Limit to 5 days
                day_date = day.get('date', '')
                day_name = day.get('day_name', '')
                events = day.get('events', [])
                is_today = day.get('is_today', False)
                
                if not events:
                    continue
                    
                # Calculate this day's section height and position
                section_y = float(y_position)
                
                # Draw day header with rounded rectangle and gradient
                header_height = float(35)
                
                # Check if this day is today and adjust the header color
                header_color = vibrant_colors["primary"] if is_today else vibrant_colors["day_header"]
                
                # Create header coordinates
                header_coords = [
                    (float(width * 0.08), float(section_y)),
                    (float(width * 0.92), float(section_y + header_height))
                ]
                
                # Draw day header with rounded corners
                self._draw_rounded_rectangle(
                    draw,
                    header_coords,
                    fill=header_color,
                    radius=15
                )
                
                # Add date information with styling
                draw.text(
                    (float(width * 0.15), float(section_y + header_height/2)), 
                    day_name, 
                    fill=(255, 255, 255), 
                    font=normal_font, 
                    anchor="lm"
                )
                
                draw.text(
                    (float(width * 0.85), float(section_y + header_height/2)), 
                    day_date, 
                    fill=(255, 255, 255), 
                    font=normal_font, 
                    anchor="rm"
                )
                
                # Update position for events
                y_position += float(header_height + 5)
                
                # Draw event cards for this day
                for event_idx, event in enumerate(events[:3]):  # Limit to 3 events per day
                    # Calculate event card dimensions
                    event_height = float(45)
                    event_y = float(y_position)
                    
                    # Create event card coordinates
                    event_coords = [
                        (float(width * 0.1), float(event_y)),
                        (float(width * 0.9), float(event_y + event_height))
                    ]
                    
                    # Draw event card with rounded corners
                    self._draw_rounded_rectangle(
                        draw,
                        event_coords,
                        fill=(255, 255, 255),
                        radius=10
                    )
                    
                    # Draw colored time box on left
                    time_box_width = float(width * 0.18)
                    
                    if event.get('all_day'):
                        time_text = "ALL DAY"
                        time_box_color = vibrant_colors["highlight"]
                    else:
                        time_text = event.get('time', '')
                        time_box_color = vibrant_colors["primary"]
                    
                    # Create time box coordinates
                    time_box_coords = [
                        (float(width * 0.12), float(event_y + 5)),
                        (float(width * 0.12 + time_box_width), float(event_y + event_height - 5))
                    ]
                    
                    # Draw rounded time box
                    self._draw_rounded_rectangle(
                        draw,
                        time_box_coords,
                        fill=time_box_color,
                        radius=8
                    )
                    
                    # Draw time text in box
                    draw.text(
                        (float(width * 0.12 + time_box_width/2), float(event_y + event_height/2)),
                        time_text,
                        fill=(255, 255, 255),
                        font=small_font,
                        anchor="mm"
                    )
                    
                    # Draw event summary
                    summary = event.get('summary', '')
                    if len(summary) > 30:  # Truncate long titles
                        summary = summary[:27] + "..."
                    
                    draw.text(
                        (float(width * 0.35), float(event_y + event_height/2 - 2)),
                        summary,
                        fill=colors["text_dark"],
                        font=normal_font,
                        anchor="lm"
                    )
                    
                    # Draw location if available
                    location = event.get('location', '')
                    if location:
                        if len(location) > 35:  # Truncate long locations
                            location = location[:32] + "..."
                        
                        draw.text(
                            (float(width * 0.35), float(event_y + event_height/2 + 15)),
                            location,
                            fill=colors["text_light"],
                            font=small_font,
                            anchor="lm"
                        )
                    
                    # Update position for next event
                    y_position += float(event_height + 8)
                
                # Add "more" indicator if there are more than 3 events
                if len(events) > 3:
                    draw.text(
                        (float(width * 0.5), float(y_position)),
                        f"+{len(events) - 3} more events",
                        fill=vibrant_colors["primary"],
                        font=small_font,
                        anchor="mm"
                    )
                    y_position += 20
                
                # Add spacing between day sections
                y_position += 15
        except Exception as e:
            # Log the specific error and draw error indicator
            logging.error(f"Error in _render_direct_list_view: {str(e)}, {type(e)}")
            # Draw error indicator
            try:
                draw.rectangle([(0, 0), (100, 30)], fill=(255, 0, 0))
                draw.text((50, 15), "RENDER ERROR", fill=(255, 255, 255), font=small_font, anchor="mm")
            except:
                pass
            # Re-raise to be caught by caller
            raise