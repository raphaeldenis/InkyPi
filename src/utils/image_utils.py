import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os
import logging
import hashlib
import tempfile
import subprocess
import shutil

logger = logging.getLogger(__name__)

def get_image(image_url):
    response = requests.get(image_url)
    img = None
    if 200 <= response.status_code < 300 or response.status_code == 304:
        img = Image.open(BytesIO(response.content))
    else:
        logger.error(f"Received non-200 response from {image_url}: status_code: {response.status_code}")
    return img

def change_orientation(image, orientation):
    if orientation == 'horizontal':
        image = image.rotate(0, expand=1)
    elif orientation == 'vertical':
        image = image.rotate(90, expand=1)
    return image

def resize_image(image, desired_size, image_settings=[]):
    """
    Resize an image with advanced options for display.
    
    Args:
        image: PIL Image object
        desired_size: Tuple of (width, height) for target dimensions
        image_settings: List of strings with processing instructions
            - "preserve-aspect": Maintain aspect ratio
            - "fit": Same as preserve-aspect
            - "keep-width": Don't crop horizontally
            - "portrait-mode": Force portrait orientation
            - "zoom-X": Zoom to X% (e.g. zoom-80 = 80%)
            - "rotate-X": Rotate X degrees (e.g. rotate-90 = 90°)
            - "quality-X": X can be "high", "medium" or "low"
            - "center-X,Y": Center point as percentages (e.g. center-25,75)
    
    Returns:
        Processed PIL Image object
    """
    # Get original dimensions
    img_width, img_height = image.size
    desired_width, desired_height = desired_size
    desired_width, desired_height = int(desired_width), int(desired_height)
    
    # Initialize quality setting (affects resampling method)
    quality = "high"
    for setting in image_settings:
        if setting.startswith("quality-"):
            quality = setting.split("-")[1]
    
    # Get resampling method based on quality
    if quality == "high":
        resample_method = Image.LANCZOS  # Highest quality, slowest
    elif quality == "medium":
        resample_method = Image.BICUBIC  # Good quality, medium speed
    else:
        resample_method = Image.BILINEAR  # Lower quality, fastest
    
    # Check for custom rotation
    for setting in image_settings:
        if setting.startswith("rotate-"):
            try:
                rotation_degrees = float(setting.split("-")[1])
                # Note: expand=True ensures the entire rotated image is visible
                image = image.rotate(rotation_degrees, expand=True, resample=resample_method)
                # Update dimensions after rotation
                img_width, img_height = image.size
            except (ValueError, IndexError):
                pass
    
    # Check for portrait mode setting
    portrait_mode = "portrait-mode" in image_settings
    if portrait_mode and img_width > img_height:
        # Rotate the image to portrait orientation
        image = image.transpose(Image.ROTATE_90)
        img_width, img_height = img_height, img_width  # Update dimensions after rotation
    
    # Check for zoom level (defaults to 1.0 = 100%)
    zoom_level = 1.0
    for setting in image_settings:
        if setting.startswith("zoom-"):
            try:
                # Extract zoom percentage (e.g., zoom-80 = 80%)
                zoom_percent = float(setting.split("-")[1])
                zoom_level = zoom_percent / 100.0
                break
            except (IndexError, ValueError):
                pass
    
    # Calculate custom center point for cropping (default: center of image)
    center_x, center_y = 50, 50  # Default center point (50%, 50%)
    for setting in image_settings:
        if setting.startswith("center-"):
            try:
                center_parts = setting.split("-")[1].split(",")
                if len(center_parts) == 2:
                    center_x = float(center_parts[0])
                    center_y = float(center_parts[1])
            except (IndexError, ValueError):
                pass
    
    # Calculate aspect ratios
    img_ratio = img_width / img_height
    desired_ratio = desired_width / desired_height
    
    # Set up processing flags
    keep_width = "keep-width" in image_settings
    preserve_aspect = "preserve-aspect" in image_settings or "fit" in image_settings

    if preserve_aspect:
        # Scale the image to fit within the desired dimensions
        # while maintaining aspect ratio and applying zoom level
        if img_ratio > desired_ratio:
            # Image is wider than the target ratio
            new_width = int(desired_width * zoom_level)
            new_height = int((desired_width / img_ratio) * zoom_level)
        else:
            # Image is taller than the target ratio
            new_height = int(desired_height * zoom_level)
            new_width = int((desired_height * img_ratio) * zoom_level)
            
        # Resize the image while maintaining aspect ratio
        resized_image = image.resize((new_width, new_height), resample_method)
        
        # Create a blank image with the desired dimensions
        result = Image.new("RGB", (desired_width, desired_height), (255, 255, 255))
        
        # Paste the resized image centered in the blank image
        offset_x = (desired_width - new_width) // 2
        offset_y = (desired_height - new_height) // 2
        result.paste(resized_image, (offset_x, offset_y))
        
        return result
    else:
        # Original cropping behavior with custom center point
        # Calculate crop dimensions
        if img_ratio > desired_ratio:
            # Image is wider than desired aspect ratio
            new_width = int(img_height * desired_ratio)
            
            if not keep_width:
                # Calculate x offset based on center_x preference (as percentage)
                # 0% = left edge, 50% = center, 100% = right edge
                total_crop = img_width - new_width
                x_offset = int((center_x / 100) * total_crop)
                x_offset = max(0, min(x_offset, img_width - new_width))  # Ensure valid range
            else:
                x_offset = 0
            
            y_offset = 0
            new_height = img_height
        else:
            # Image is taller than desired aspect ratio
            new_height = int(img_width / desired_ratio)
            
            if not keep_width:
                # Calculate y offset based on center_y preference (as percentage)
                # 0% = top edge, 50% = center, 100% = bottom edge
                total_crop = img_height - new_height
                y_offset = int((center_y / 100) * total_crop)
                y_offset = max(0, min(y_offset, img_height - new_height))  # Ensure valid range
            else:
                y_offset = 0
            
            x_offset = 0
            new_width = img_width

        # Crop the image
        cropped_image = image.crop((x_offset, y_offset, x_offset + new_width, y_offset + new_height))

        # Resize to the exact desired dimensions
        return cropped_image.resize((desired_width, desired_height), resample_method)

def compute_image_hash(image):
    """Compute SHA-256 hash of an image."""
    image = image.convert("RGB")
    img_bytes = image.tobytes()
    return hashlib.sha256(img_bytes).hexdigest()

def take_screenshot_html(html_str, dimensions):
    """Take a screenshot of rendered HTML content."""
    image = None
    
    # Check if chromium-browser is available
    chromium_available = shutil.which("chromium-browser") is not None
    
    if chromium_available:
        try:
            # Create a temporary HTML file
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as html_file:
                html_file.write(html_str.encode("utf-8"))
                html_file_path = html_file.name

            # Create a temporary output file for the screenshot
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as img_file:
                img_file_path = img_file.name

            command = [
                "chromium-browser", html_file_path, "--headless=old",
                f"--screenshot={img_file_path}", f'--window-size={dimensions[0]},{dimensions[1]}',
                "--no-sandbox", "--disable-gpu", "--disable-software-rasterizer",
                "--disable-dev-shm-usage", "--hide-scrollbars"
            ]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Check if the process failed or the output file is missing
            if result.returncode != 0 or not os.path.exists(img_file_path):
                logger.error("Failed to take screenshot:")
                logger.error(result.stderr.decode('utf-8'))
                return render_fallback_image(dimensions, "")

            # Load the image using PIL
            image = Image.open(img_file_path)

            # Cleanup temp files
            os.remove(html_file_path)
            os.remove(img_file_path)

        except Exception as e:
            logger.error(f"Failed to take screenshot: {str(e)}")
            return render_fallback_image(dimensions, "")
    else:
        # Fallback to basic rendering when chromium is not available
        logger.warning("chromium-browser not found, using fallback rendering method")
        image = render_fallback_image(dimensions, "")
    
    return image

def render_fallback_image(dimensions, message="Fallback image"):
    """Create a basic fallback image when HTML rendering is not available."""
    width, height = dimensions
    image = Image.new("RGB", dimensions, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # Try to get a font, or use default
    try:
        from utils.app_utils import get_font
        font_large = get_font("Jost-SemiBold", 32)
        font_medium = get_font("Jost", 24)
    except:
        # Use a default font
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
    
    # Only draw the message if it's not the default browser warning and not empty
    if message and "browser" not in message.lower() and "chromium" not in message.lower():
        # Draw message
        message_lines = message.split('\n')
        y_position = height // 2
        line_height = 30
        
        for line in message_lines:
            draw.text((width//2, y_position), line, fill=(0, 0, 0), font=font_medium, anchor="mm")
            y_position += line_height
    
    return image