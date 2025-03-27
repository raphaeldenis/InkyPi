from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
from io import BytesIO
import logging
from utils.image_utils import resize_image

logger = logging.getLogger(__name__)

class ImageUpload(BasePlugin):
    def generate_image(self, settings, device_config):
        img_index = settings.get("image_index", 0)
        image_locations = settings.get("imageFiles[]")

        if img_index >= len(image_locations):
            # reset if image_locations changed
            img_index = 0

        if not image_locations:
            raise RuntimeError("No images provided.")
        
        # Collect display settings
        image_settings = ["preserve-aspect"]  # Always preserve aspect ratio
        
        # Add portrait mode if requested
        if settings.get("portraitMode") == "true":
            image_settings.append("portrait-mode")
            
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
            
            # Get display dimensions
            dimensions = device_config.get_resolution()
            if device_config.get_config("orientation") == "vertical":
                dimensions = dimensions[::-1]
                
            # Use resize_image with our collected settings
            image = resize_image(image, dimensions, image_settings)
            
            settings['image_index'] = (img_index + 1) % len(image_locations)
            return image
        except Exception as e:
            logger.error(f"Failed to read image file: {str(e)}")
            raise RuntimeError("Failed to read image file.")