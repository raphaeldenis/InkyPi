import copy
import hashlib
import importlib.util
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from config import Config
from plugins.image_upload.image_upload import ImageUpload
from refresh_task import PlaylistRefresh, RefreshTask

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_SCRIPT = REPO_ROOT / "scripts" / "migrate_device_config.py"
PLUGINS_DIR = REPO_ROOT / "src" / "plugins"
FORK_SHAPE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "device_fork_shape.json"

CHOSEN_CYCLE_INTERVAL_SECONDS = 1800
SCHEDULER_SLEEP_TIMES_THAT_MUST_NOT_LEAK = (60, 7)
FORK_IMAGE_UPLOAD_KEYS = (
    "timerEnabled",
    "timerValue",
    "timerUnit",
    "portraitMode",
    "zoomLevel",
    "rotation",
    "quality",
    "centerX",
    "centerY",
)


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("migrate_device_config", MIGRATION_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fork_config():
    return json.loads(FORK_SHAPE_FIXTURE.read_text())


@pytest.fixture
def migrate(migration):
    def run(config, **option_overrides):
        known_plugin_ids = migration.discover_known_plugin_ids(PLUGINS_DIR)
        options = migration.MigrationOptions(
            display_type="inky",
            known_plugin_ids=known_plugin_ids,
            plugin_cycle_interval_seconds=CHOSEN_CYCLE_INTERVAL_SECONDS,
            **option_overrides,
        )
        return migration.migrate_device_config(config, options)

    return run


@pytest.fixture
def migrate_with_report(migration):
    def run(config):
        options = migration.MigrationOptions(
            display_type="inky",
            known_plugin_ids=migration.discover_known_plugin_ids(PLUGINS_DIR),
            plugin_cycle_interval_seconds=CHOSEN_CYCLE_INTERVAL_SECONDS,
        )
        return migration.migrate_device_config_with_report(config, options)

    return run


def instances_of(config, plugin_id):
    return [
        instance
        for playlist in config["playlist_config"]["playlists"]
        for instance in playlist["plugins"]
        if instance["plugin_id"] == plugin_id
    ]


def instance_named(config, name):
    return next(
        instance
        for playlist in config["playlist_config"]["playlists"]
        for instance in playlist["plugins"]
        if instance["name"] == name
    )


def test_migration_adds_upstream_keys_without_touching_playlists_order(migrate, fork_config):
    playlist_names_before = [p["name"] for p in fork_config["playlist_config"]["playlists"]]

    migrated = migrate(fork_config)

    playlist_names_after = [p["name"] for p in migrated["playlist_config"]["playlists"]]
    assert migrated["display_type"] == "inky"
    assert migrated["inverted_image"] is False
    assert "plugin_order" not in migrated
    assert playlist_names_after == playlist_names_before


def test_migration_does_not_modify_the_config_it_receives(migrate, fork_config):
    untouched_copy = copy.deepcopy(fork_config)

    migrate(fork_config)

    assert fork_config == untouched_copy


def test_migration_drops_instances_of_removed_plugins(migrate_with_report, fork_config):
    result = migrate_with_report(fork_config)

    assert instances_of(result.config, "icalendar") == []
    assert instances_of(result.config, "icloud_calendar") == []
    assert sorted(result.removed_instance_names) == ["Agenda", "Family"]
    assert [i["name"] for i in instances_of(result.config, "weather")] == ["Meteo"]


@pytest.mark.parametrize("scheduler_sleep_time", SCHEDULER_SLEEP_TIMES_THAT_MUST_NOT_LEAK)
def test_migration_never_derives_cycle_interval_from_scheduler_sleep_time(migrate, fork_config, scheduler_sleep_time):
    fork_config["scheduler_sleep_time"] = scheduler_sleep_time

    migrated = migrate(fork_config)

    assert migrated["plugin_cycle_interval_seconds"] == CHOSEN_CYCLE_INTERVAL_SECONDS


def test_migration_requires_the_display_type_to_be_chosen(migration):
    with pytest.raises(TypeError):
        migration.MigrationOptions(known_plugin_ids=frozenset())


def test_migration_keeps_the_display_type_already_present(migrate, fork_config):
    fork_config["display_type"] = "mock"

    migrated = migrate(fork_config)

    assert migrated["display_type"] == "mock"


def test_migration_is_idempotent(migrate, fork_config):
    migrated_once = migrate(fork_config)

    migrated_twice = migrate(migrated_once)

    assert migrated_twice == migrated_once


def test_migration_applies_the_per_instance_intervals(migrate, fork_config):
    migrated = migrate(fork_config)

    assert instance_named(migrated, "Meteo")["refresh"] == {"interval": 3600}
    assert instance_named(migrated, "Photos")["refresh"] == {"interval": 600}
    assert instance_named(migrated, "Clock")["refresh"] == {"interval": 3600}


def test_migration_strips_the_fork_keys_from_image_upload_and_randomizes(migrate, fork_config):
    migrated = migrate(fork_config)

    photo_settings = instance_named(migrated, "Photos")["plugin_settings"]
    assert all(key not in photo_settings for key in FORK_IMAGE_UPLOAD_KEYS)
    assert photo_settings["randomize"] == "true"
    assert photo_settings["imageFiles[]"] == ["/var/example/photos/one.png", "/var/example/photos/two.png"]


def test_migration_leaves_photos_sequential_when_randomize_is_declined(migrate, fork_config):
    migrated = migrate(fork_config, randomize_photos=False)

    assert "randomize" not in instance_named(migrated, "Photos")["plugin_settings"]


def test_migration_sets_french_on_weather_instances_without_a_language(migrate, fork_config):
    migrated = migrate(fork_config)

    assert instance_named(migrated, "Meteo")["plugin_settings"]["language"] == "fr"


def test_migration_keeps_the_language_a_weather_instance_already_has(migrate, fork_config):
    instance_named(fork_config, "Meteo")["plugin_settings"]["language"] = "en"

    migrated = migrate(fork_config)

    assert instance_named(migrated, "Meteo")["plugin_settings"]["language"] == "en"


def test_migration_never_creates_a_calendar_instance(migrate, fork_config):
    migrated = migrate(fork_config)

    assert instances_of(migrated, "calendar") == []


def test_migrated_config_boots_the_app(migrate, fork_config, make_app):
    migrated = migrate(fork_config)
    client = make_app(extra_config=migrated).test_client()

    home_response = client.get("/")
    playlist_response = client.get("/playlist")

    assert home_response.status_code == 200
    assert playlist_response.status_code == 200


def test_cli_writes_the_migrated_copy_and_never_the_input(migration, migrate, fork_config, tmp_path, capsys):
    input_copy = tmp_path / "input_copy.json"
    output_file = tmp_path / "migrated.json"
    input_copy.write_text(json.dumps(fork_config))
    sha256_before = hashlib.sha256(input_copy.read_bytes()).hexdigest()

    exit_code = migration.main(
        [
            "--in", str(input_copy),
            "--out", str(output_file),
            "--display-type", "inky",
            "--cycle-interval-seconds", str(CHOSEN_CYCLE_INTERVAL_SECONDS),
            "--plugins-dir", str(PLUGINS_DIR),
        ]
    )

    sha256_after = hashlib.sha256(input_copy.read_bytes()).hexdigest()
    report = capsys.readouterr().out
    assert exit_code == 0
    assert sha256_after == sha256_before
    assert json.loads(output_file.read_text()) == migrate(fork_config)
    assert "Agenda" in report and "Family" in report


def test_cli_refuses_to_write_over_its_input(migration, fork_config, tmp_path):
    input_copy = tmp_path / "input_copy.json"
    input_copy.write_text(json.dumps(fork_config))
    sha256_before = hashlib.sha256(input_copy.read_bytes()).hexdigest()

    exit_code = migration.main(
        ["--in", str(input_copy), "--out", str(input_copy), "--display-type", "inky", "--plugins-dir", str(PLUGINS_DIR)]
    )

    assert exit_code != 0
    assert hashlib.sha256(input_copy.read_bytes()).hexdigest() == sha256_before


class RecordingPlugin:
    """Stands in for a plugin and counts the renders it is asked for."""

    def __init__(self, rendered_color):
        self.rendered_color = rendered_color
        self.render_count = 0

    def generate_image(self, settings, device_config):
        self.render_count += 1
        return Image.new("RGB", (800, 480), self.rendered_color)


def device_config_from(write_device_json, config):
    write_device_json(config)
    return Config()


def test_instance_within_its_interval_shows_its_cached_image_without_rendering(
    migrate, fork_config, write_device_json
):
    device_config = device_config_from(write_device_json, migrate(fork_config))
    playlist = device_config.get_playlist_manager().get_playlist("Default")
    photos = playlist.find_plugin("image_upload", "Photos")
    first_shown_at = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
    plugin = RecordingPlugin(rendered_color=(0, 0, 255))
    cached_image_path = Path(device_config.plugin_image_dir) / photos.get_image_path()
    Image.new("RGB", (800, 480), (255, 0, 0)).save(cached_image_path)
    photos.latest_refresh_time = first_shown_at.isoformat()
    still_within_interval = first_shown_at + timedelta(seconds=photos.refresh["interval"] - 1)
    interval_elapsed = first_shown_at + timedelta(seconds=photos.refresh["interval"])

    cached = PlaylistRefresh(playlist, photos).execute(plugin, device_config, still_within_interval)
    renders_while_cached = plugin.render_count
    rendered = PlaylistRefresh(playlist, photos).execute(plugin, device_config, interval_elapsed)

    assert cached.getpixel((0, 0)) == (255, 0, 0)
    assert renders_while_cached == 0
    assert rendered.getpixel((0, 0)) == (0, 0, 255)
    assert plugin.render_count == 1


def test_photo_frame_rotates_photos_on_playlist_interval(
    migrate, fork_config, write_device_json, tmp_path, monkeypatch
):
    photo_paths = []
    for color_name, color in (("red", (255, 0, 0)), ("blue", (0, 0, 255))):
        photo_path = tmp_path / f"{color_name}.png"
        Image.new("RGB", (800, 480), color).save(photo_path)
        photo_paths.append(str(photo_path))
    instance_named(fork_config, "Photos")["plugin_settings"]["imageFiles[]"] = photo_paths
    device_config = device_config_from(write_device_json, migrate(fork_config))
    playlist = device_config.get_playlist_manager().get_playlist("Default")
    photos = playlist.find_plugin("image_upload", "Photos")
    plugin = ImageUpload({"id": "image_upload"})
    drawn_indexes = iter([1, 0])
    monkeypatch.setattr("plugins.image_upload.image_upload.random.randrange", lambda start, stop: next(drawn_indexes))
    frozen_start = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
    second_cycle = frozen_start + timedelta(seconds=photos.refresh["interval"] + 1)

    first_photo = PlaylistRefresh(playlist, photos).execute(plugin, device_config, frozen_start)
    second_photo = PlaylistRefresh(playlist, photos).execute(plugin, device_config, second_cycle)

    assert first_photo.getpixel((400, 240)) == (0, 0, 255)
    assert second_photo.getpixel((400, 240)) == (255, 0, 0)


class ScreenRecorder:
    def __init__(self):
        self.shown_images = []

    def display_image(self, image, image_settings=None):
        self.shown_images.append(image)


def test_refresh_skips_unknown_plugin_id_without_crashing(fork_config, write_device_json, caplog):
    only_removed_plugins = copy.deepcopy(fork_config)
    only_removed_plugins["plugin_cycle_interval_seconds"] = 0.05
    only_removed_plugins["playlist_config"]["playlists"] = [
        playlist for playlist in only_removed_plugins["playlist_config"]["playlists"] if playlist["name"] == "Default"
    ]
    default_playlist = only_removed_plugins["playlist_config"]["playlists"][0]
    default_playlist["plugins"] = [i for i in default_playlist["plugins"] if i["plugin_id"] == "icalendar"]
    screen = ScreenRecorder()
    refresh_task = RefreshTask(device_config_from(write_device_json, only_removed_plugins), screen)
    caplog.set_level(logging.ERROR)

    refresh_task.start()
    deadline = time.monotonic() + 5
    while "Plugin config not found for 'icalendar'" not in caplog.text and time.monotonic() < deadline:
        time.sleep(0.02)
    refresh_thread_survived = refresh_task.thread.is_alive()
    refresh_task.stop()

    assert "Plugin config not found for 'icalendar'" in caplog.text
    assert refresh_thread_survived
    assert screen.shown_images == []
