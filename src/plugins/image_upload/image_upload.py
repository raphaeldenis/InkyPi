from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
from io import BytesIO
import logging
from utils.image_utils import resize_image
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ImageUpload(BasePlugin):
    # Keep track of last image change time
    last_change_time = None
    
    def generate_image(self, settings, device_config):
        img_index = settings.get("image_index", 0)
        image_locations = settings.get("imageFiles[]")

        # Handle timer functionality
        if settings.get("timerEnabled") == "true" and len(image_locations) > 1:
            # Check if we need to change the image based on timer
            current_time = datetime.now()
            
            # Convert timer value and unit to seconds
            timer_value = int(settings.get("timerValue", 1))
            timer_unit = settings.get("timerUnit", "m")
            
            # Calculate seconds based on unit
            seconds_multiplier = {
                "s": 1,
                "m": 60,
                "h": 3600,
                "d": 86400
            }
            timer_seconds = timer_value * seconds_multiplier.get(timer_unit, 60)
            
            # Initialize last_change_time if it's None
            if ImageUpload.last_change_time is None:
                ImageUpload.last_change_time = current_time
            
            # Check if it's time to change the image
            time_diff = (current_time - ImageUpload.last_change_time).total_seconds()
            if time_diff >= timer_seconds:
                # Update the image index
                img_index = (img_index + 1) % len(image_locations)
                ImageUpload.last_change_time = current_time
                # Save the updated index back to settings
                settings['image_index'] = img_index
                logger.info(f"Timer triggered image change to index {img_index}")

        if img_index >= len(image_locations):
            # reset if image_locations changed
            img_index = 0

        if not image_locations:
            raise RuntimeError("No images provided.")
        
        # Collect display settings
        image_settings = ["preserve-aspect"]  # Always preserve aspect ratio
        
        # Get display orientation
        device_orientation = device_config.get_config("orientation", "horizontal")
        
        # Get display dimensions
        dimensions = device_config.get_resolution()
        # Store original dimensions (make a copy for logging)
        original_dimensions = tuple(dimensions) if isinstance(dimensions, list) else dimensions
        
        # Check if portrait mode should be applied
        portrait_mode_enabled = settings.get("portraitMode") == "true"
        
        # Simplify the logic for portrait mode
        if portrait_mode_enabled:
            # If portrait mode is enabled, always add the setting
            image_settings.append("portrait-mode")
            
            # Ensure dimensions are correctly set for portrait orientation
            # For eInk displays, we often need to rotate the image 90 degrees
            if  device_orientation != "vertical":  # If device is not already in vertical orientation
                # For portrait mode on horizontal device, swap width and height
                width, height = dimensions
                dimensions = (height, width)
            
        # Add zoom level if specified
        zoom_level = settings.get("zoomLevel", "100")
        if zoom_level != "100":  # Only add if not 100%
            image_settings.append(f"zoom-{zoom_level}")
            
        # Add rotation if specified
        rotation = settings.get("rotation")
        if rotation:
            image_settings.append(f"rotate-{rotation}")
            
        # Add quality setting if specified
        quality = settings.get("quality", "high")
        image_settings.append(f"quality-{quality}")
        
        # Add custom center point if specified
        center_x = settings.get("centerX")
        center_y = settings.get("centerY")
        if center_x and center_y:
            image_settings.append(f"center-{center_x},{center_y}")
            
        # Open the image using Pillow
        try:
            image = Image.open(image_locations[img_index])
            
            # Log dimensions for debugging
            logger.info(f"Original image dimensions: {image.size}")
            logger.info(f"Display dimensions: {dimensions}")
            logger.info(f"Portrait mode enabled: {portrait_mode_enabled}")
            logger.info(f"Device orientation: {device_orientation}")
            logger.info(f"Image settings: {image_settings}")
            
            # Use resize_image with our collected settings
            image = resize_image(image, dimensions, image_settings)
            
            # Only increment the image index if timer is not enabled
            if settings.get("timerEnabled") != "true":
                settings['image_index'] = (img_index + 1) % len(image_locations)
            return image
        except Exception as e:
            logger.error(f"Failed to read image file: {str(e)}")
            raise RuntimeError(f"Failed to read image file: {str(e)}")