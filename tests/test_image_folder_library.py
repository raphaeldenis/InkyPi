import pytest
from PIL import Image

from config import Config
from plugins.plugin_registry import get_plugin_instance, load_plugins

LANDSCAPE_RESOLUTION = [800, 480]
EDGE_STRIPE_WIDTH = 60
RED = (220, 20, 20)
BLUE = (20, 20, 220)
GREEN = (20, 220, 20)
DRAWS_TO_SEE_EVERY_FILE = 40


def build_image_folder_plugin(write_device_json, orientation):
    write_device_json(
        {
            "name": "InkyPi test",
            "display_type": "mock",
            "resolution": LANDSCAPE_RESOLUTION,
            "orientation": orientation,
            "timezone": "Europe/Paris",
            "time_format": "24h",
        }
    )
    device_config = Config()
    load_plugins(device_config.get_plugins())
    return get_plugin_instance({"id": "image_folder"}), device_config


def folder_settings(folder, keeps_whole_photo):
    return {
        "folder_path": str(folder),
        "padImage": "true" if keeps_whole_photo else "false",
        "backgroundOption": "blur",
    }


def save_landscape_photo_with_red_left_edge(path):
    photo = Image.new("RGB", (800, 600), BLUE)
    photo.paste(Image.new("RGB", (EDGE_STRIPE_WIDTH, 600), RED), (0, 0))
    photo.save(path, quality=95)


def mostly(pixel, expected, tolerance=40):
    return all(abs(channel - wanted) <= tolerance for channel, wanted in zip(pixel, expected))


@pytest.fixture
def library_folder(tmp_path):
    folder = tmp_path / "library"
    folder.mkdir()
    save_landscape_photo_with_red_left_edge(folder / "0123456789abcdef.jpg")
    Image.new("RGB", (600, 800), BLUE).save(folder / "fedcba9876543210.jpg", quality=90)
    return folder


@pytest.mark.parametrize("orientation, expected_size", [("horizontal", (800, 480)), ("vertical", (480, 800))])
@pytest.mark.parametrize("keeps_whole_photo", [True, False])
def test_image_folder_displays_a_filled_library_at_the_exact_screen_size(
    write_device_json, library_folder, orientation, expected_size, keeps_whole_photo
):
    plugin, device_config = build_image_folder_plugin(write_device_json, orientation)

    shown = plugin.generate_image(folder_settings(library_folder, keeps_whole_photo), device_config)

    center_of_the_screen = (expected_size[0] // 2, expected_size[1] // 2)
    assert shown.size == expected_size
    assert shown.mode == "RGB"
    assert mostly(shown.getpixel(center_of_the_screen), BLUE)


def test_whole_photo_with_blur_keeps_the_edge_that_cropping_cuts_off(write_device_json, tmp_path):
    folder = tmp_path / "single"
    folder.mkdir()
    save_landscape_photo_with_red_left_edge(folder / "0123456789abcdef.jpg")
    plugin, device_config = build_image_folder_plugin(write_device_json, "vertical")
    middle_of_the_left_edge = (4, 400)

    whole_photo = plugin.generate_image(folder_settings(folder, keeps_whole_photo=True), device_config)
    cropped_photo = plugin.generate_image(folder_settings(folder, keeps_whole_photo=False), device_config)

    assert mostly(whole_photo.getpixel(middle_of_the_left_edge), RED)
    assert not mostly(cropped_photo.getpixel(middle_of_the_left_edge), RED)


def test_image_folder_never_draws_hidden_or_partial_transfer_files(write_device_json, tmp_path):
    folder = tmp_path / "during_a_transfer"
    folder.mkdir()
    Image.new("RGB", (800, 600), BLUE).save(folder / "0123456789abcdef.jpg", quality=90)
    Image.new("RGB", (800, 600), GREEN).save(folder / ".hidden_photo.jpg", format="JPEG")
    Image.new("RGB", (800, 600), GREEN).save(folder / ".fedcba9876543210.jpg.AbC123", format="JPEG")
    plugin, device_config = build_image_folder_plugin(write_device_json, "horizontal")
    center_of_the_screen = (400, 240)

    drawn_center_pixels = [
        plugin.generate_image(folder_settings(folder, keeps_whole_photo=True), device_config).getpixel(center_of_the_screen)
        for _ in range(DRAWS_TO_SEE_EVERY_FILE)
    ]

    assert all(mostly(pixel, BLUE) for pixel in drawn_center_pixels)
    assert not any(mostly(pixel, GREEN) for pixel in drawn_center_pixels)
