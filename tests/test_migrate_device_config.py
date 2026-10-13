import copy
import hashlib
import importlib.util
import json
import logging
import os
import sys
import threading
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
    assert [i["name"] for i in instances_of(result.config, "weather")] == ["Meteo", "MeteoMatin"]


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
    evening_playlist = fork_config["playlist_config"]["playlists"][1]
    evening_playlist["plugins"] = [i for i in evening_playlist["plugins"] if i["plugin_id"] != "calendar"]

    migrated = migrate(fork_config)

    assert instances_of(migrated, "calendar") == []


def test_migrated_config_is_loadable_by_the_app(migrate, fork_config, make_app):
    """The output is a config the app loads and serves on its home and playlist pages."""
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
    only_removed_plugins["refresh_info"] = {}
    only_removed_plugins["playlist_config"]["playlists"] = [
        playlist for playlist in only_removed_plugins["playlist_config"]["playlists"] if playlist["name"] == "Default"
    ]
    default_playlist = only_removed_plugins["playlist_config"]["playlists"][0]
    default_playlist["plugins"] = [i for i in default_playlist["plugins"] if i["plugin_id"] == "icalendar"]
    default_playlist["current_plugin_index"] = None
    screen = ScreenRecorder()
    refresh_task = RefreshTask(device_config_from(write_device_json, only_removed_plugins), screen)
    caplog.set_level(logging.ERROR)

    unknown_plugin_logged = threading.Event()

    def signal_when_unknown_plugin_is_logged(record):
        if "Plugin config not found for 'icalendar'" in record.getMessage():
            unknown_plugin_logged.set()
        return True

    caplog.handler.addFilter(signal_when_unknown_plugin_is_logged)

    refresh_task.start()
    unknown_plugin_was_logged = unknown_plugin_logged.wait(timeout=5)
    refresh_thread_survived = refresh_task.thread.is_alive()
    refresh_task.stop()

    assert unknown_plugin_was_logged
    assert refresh_thread_survived
    assert screen.shown_images == []


# --- preservation of what the migration must not touch -------------------------------------------

SENTINEL_KEY = "sentinel_setting_key_7f3a"
SENTINEL_VALUE = "sentinel-setting-value-9c1d"
KEPT_SENTINEL_KEY = "kept_setting_key_2b8e"
KEPT_SENTINEL_VALUE = "kept-setting-value-4d6f"
EXPECTED_PLAYLIST_ORDER = ["Default", "Evening"]
EXPECTED_INSTANCE_ORDER = {"Default": ["Meteo", "MeteoMatin", "Photos"], "Evening": ["Clock", "Planning", "Folder"]}


def test_migration_applies_the_d7_intervals_whatever_the_initial_ones(migrate, fork_config):
    migrated = migrate(fork_config)

    assert instance_named(migrated, "Meteo")["refresh"]["interval"] == 3600
    assert instance_named(migrated, "Planning")["refresh"]["interval"] == 10800
    assert instance_named(migrated, "Photos")["refresh"]["interval"] == 600
    assert instance_named(migrated, "Folder")["refresh"]["interval"] == 600


def test_migration_gives_no_interval_to_a_scheduled_instance(migrate, fork_config):
    migrated = migrate(fork_config)

    assert instance_named(migrated, "MeteoMatin")["refresh"] == {"scheduled": "07:30"}


def test_migration_sets_the_language_on_a_weather_instance_without_settings(migrate, fork_config):
    migrated = migrate(fork_config)

    assert instance_named(migrated, "MeteoMatin")["plugin_settings"] == {"language": "fr"}


def test_migration_sets_the_language_only_on_weather(migrate, fork_config):
    migrated = migrate(fork_config)

    other_instances = [i for p in migrated["playlist_config"]["playlists"] for i in p["plugins"] if i["plugin_id"] != "weather"]
    assert other_instances
    assert all("language" not in i.get("plugin_settings", {}) for i in other_instances)


def test_migration_strips_the_fork_keys_only_from_image_upload(migrate, fork_config):
    migrated = migrate(fork_config)

    folder_settings = instance_named(migrated, "Folder")["plugin_settings"]
    assert folder_settings == {"folder_path": "/var/example/folder", "rotation": "90", "timerEnabled": "true"}


def test_migration_keeps_an_existing_inverted_image(migrate, fork_config):
    fork_config["inverted_image"] = True

    migrated = migrate(fork_config)

    assert migrated["inverted_image"] is True


def with_unknown_keys_everywhere(config):
    config = copy.deepcopy(config)
    config["unknown_key"] = "kept"
    config["playlist_config"]["unknown_key"] = "kept"
    for playlist in config["playlist_config"]["playlists"]:
        playlist["unknown_key"] = "kept"
        for instance in playlist["plugins"]:
            instance["unknown_key"] = "kept"
            for nested_name in ("plugin_settings", "refresh"):
                if nested_name in instance:
                    instance[nested_name]["unknown_key"] = "kept"
    return config


def keyed_by_name(config):
    keyed = copy.deepcopy(config)
    playlists = keyed["playlist_config"]["playlists"]
    for playlist in playlists:
        playlist["plugins"] = {instance["name"]: instance for instance in playlist["plugins"]}
    keyed["playlist_config"]["playlists"] = {playlist["name"]: playlist for playlist in playlists}
    return keyed


def structural_changes(before, after, path=()):
    both_are_objects = isinstance(before, dict) and isinstance(after, dict)
    if not both_are_objects:
        return {} if before == after else {path: ("changed", before, after)}
    changes = {}
    for key in before.keys() | after.keys():
        if key not in after:
            changes[path + (key,)] = ("removed", before[key])
        elif key not in before:
            changes[path + (key,)] = ("added", after[key])
        else:
            changes.update(structural_changes(before[key], after[key], path + (key,)))
    return changes


def test_migration_changes_only_what_it_is_meant_to_change(migrate, fork_config):
    unknown_keys_config = with_unknown_keys_everywhere(fork_config)
    before = keyed_by_name(unknown_keys_config)
    playlists_path = ("playlist_config", "playlists")

    def instance_path(playlist_name, instance_name, *rest):
        return playlists_path + (playlist_name, "plugins", instance_name) + rest

    expected_changes = {
        ("display_type",): ("added", "inky"),
        ("inverted_image",): ("added", False),
        ("plugin_cycle_interval_seconds",): ("changed", 3600, CHOSEN_CYCLE_INTERVAL_SECONDS),
        instance_path("Default", "Agenda"): ("removed", before["playlist_config"]["playlists"]["Default"]["plugins"]["Agenda"]),
        instance_path("Evening", "Family"): ("removed", before["playlist_config"]["playlists"]["Evening"]["plugins"]["Family"]),
        instance_path("Default", "Meteo", "refresh", "interval"): ("changed", 1234, 3600),
        instance_path("Default", "Meteo", "plugin_settings", "language"): ("added", "fr"),
        instance_path("Default", "MeteoMatin", "plugin_settings"): ("added", {"language": "fr"}),
        instance_path("Default", "Photos", "refresh", "interval"): ("changed", 3600, 600),
        instance_path("Default", "Photos", "plugin_settings", "randomize"): ("added", "true"),
        instance_path("Evening", "Planning", "refresh", "interval"): ("changed", 1234, 10800),
        instance_path("Evening", "Folder", "refresh", "interval"): ("changed", 1234, 600),
    }
    photo_settings = before["playlist_config"]["playlists"]["Default"]["plugins"]["Photos"]["plugin_settings"]
    for fork_key in FORK_IMAGE_UPLOAD_KEYS:
        expected_changes[instance_path("Default", "Photos", "plugin_settings", fork_key)] = ("removed", photo_settings[fork_key])

    migrated = migrate(unknown_keys_config)

    assert structural_changes(before, keyed_by_name(migrated)) == expected_changes
    assert [p["name"] for p in migrated["playlist_config"]["playlists"]] == EXPECTED_PLAYLIST_ORDER
    for playlist in migrated["playlist_config"]["playlists"]:
        assert [i["name"] for i in playlist["plugins"]] == EXPECTED_INSTANCE_ORDER[playlist["name"]]


# --- refusals of the pure function ----------------------------------------------------------------


def migration_options(migration, **overrides):
    settings = {"display_type": "inky", "known_plugin_ids": frozenset({"weather"})}
    settings.update(overrides)
    return migration.MigrationOptions(**settings)


def test_migration_refuses_an_empty_set_of_known_plugins(migration, fork_config):
    options = migration_options(migration, known_plugin_ids=frozenset())

    with pytest.raises(migration.MigrationRefused):
        migration.migrate_device_config(fork_config, options)


@pytest.mark.parametrize("display_type", ["inky", "mock", "epd7in3e"])
def test_migration_accepts_the_display_types_the_app_supports(migration, fork_config, display_type):
    options = migration_options(migration, display_type=display_type)

    migrated = migration.migrate_device_config(fork_config, options)

    assert migrated["display_type"] == display_type


@pytest.mark.parametrize("display_type", ["", "lcd", "inkyy", "epd"])
def test_migration_refuses_a_display_type_the_app_does_not_support(migration, fork_config, display_type):
    options = migration_options(migration, display_type=display_type)

    with pytest.raises(migration.MigrationRefused, match="display"):
        migration.migrate_device_config(fork_config, options)


@pytest.mark.parametrize("interval_seconds", [1, 86400])
def test_migration_accepts_the_cycle_intervals_the_app_accepts(migration, fork_config, interval_seconds):
    options = migration_options(migration, plugin_cycle_interval_seconds=interval_seconds)

    migrated = migration.migrate_device_config(fork_config, options)

    assert migrated["plugin_cycle_interval_seconds"] == interval_seconds


@pytest.mark.parametrize("interval_seconds", [0, -1, 86401, 1.5, "1800", True])
def test_migration_refuses_a_cycle_interval_the_app_rejects(migration, fork_config, interval_seconds):
    options = migration_options(migration, plugin_cycle_interval_seconds=interval_seconds)

    with pytest.raises(migration.MigrationRefused, match="interval"):
        migration.migrate_device_config(fork_config, options)


# --- plugin discovery -----------------------------------------------------------------------------


def test_discover_known_plugin_ids_lists_the_folders_with_a_plugin_info_file(migration, tmp_path):
    (tmp_path / "weather").mkdir()
    (tmp_path / "weather" / "plugin-info.json").write_text("{}")
    (tmp_path / "half_removed").mkdir()
    (tmp_path / "half_removed" / "readme.md").write_text("")
    (tmp_path / "loose_file.txt").write_text("")

    known_plugin_ids = migration.discover_known_plugin_ids(tmp_path)

    assert known_plugin_ids == frozenset({"weather"})


def test_discover_known_plugin_ids_reads_the_upstream_plugins(migration):
    known_plugin_ids = migration.discover_known_plugin_ids(PLUGINS_DIR)

    assert {"weather", "calendar", "image_upload", "image_folder", "clock"} <= known_plugin_ids
    assert not {"icalendar", "icloud_calendar"} & known_plugin_ids


def test_discover_known_plugin_ids_refuses_a_missing_folder(migration, tmp_path):
    with pytest.raises(migration.MigrationRefused):
        migration.discover_known_plugin_ids(tmp_path / "absent")


# --- command line ---------------------------------------------------------------------------------


FORK_FIXTURE = object()


@pytest.fixture
def run_cli(migration, fork_config, tmp_path, capsys):
    """Runs the CLI over a written input copy and returns what a person would observe."""
    input_copy = tmp_path / "input_copy.json"
    output_file = tmp_path / "migrated.json"

    def run(*extra_arguments, config=FORK_FIXTURE, plugins_dir=PLUGINS_DIR, display_type="inky", raw_input_text=None):
        input_copy.write_text(raw_input_text or json.dumps(fork_config if config is FORK_FIXTURE else config))
        arguments = ["--in", str(input_copy), "--out", str(output_file), "--display-type", display_type]
        arguments += ["--plugins-dir", str(plugins_dir)]
        exit_code = migration.main(arguments + list(extra_arguments))
        captured = capsys.readouterr()
        written = output_file.read_text() if output_file.exists() else None
        return exit_code, captured.out, captured.err, written

    run.input_copy = input_copy
    run.output_file = output_file
    return run


def make_plugins_dir(root, plugin_ids):
    for plugin_id in plugin_ids:
        (root / plugin_id).mkdir(parents=True)
        (root / plugin_id / "plugin-info.json").write_text("{}")
    return root


def test_cli_applies_every_non_default_option(run_cli, tmp_path):
    plugins_dir = make_plugins_dir(tmp_path / "only_two_plugins", ["weather", "image_upload"])

    exit_code, stdout, _, written = run_cli(
        "--cycle-interval-seconds", "7200", "--no-randomize", plugins_dir=plugins_dir, display_type="mock"
    )

    migrated = json.loads(written)
    assert exit_code == 0
    assert migrated["display_type"] == "mock"
    assert migrated["plugin_cycle_interval_seconds"] == 7200
    assert "randomize" not in instance_named(migrated, "Photos")["plugin_settings"]
    assert [i["name"] for p in migrated["playlist_config"]["playlists"] for i in p["plugins"]] == ["Meteo", "MeteoMatin", "Photos"]
    assert "kept 3 / removed 5" in stdout


def test_cli_reports_the_kept_and_removed_counts_and_the_removed_names(run_cli):
    exit_code, stdout, _, _ = run_cli()

    assert exit_code == 0
    assert "kept 6 / removed 2" in stdout
    assert "Agenda" in stdout and "Family" in stdout


def test_cli_never_prints_nor_writes_the_settings_of_removed_instances(run_cli, fork_config):
    secret_settings = {SENTINEL_KEY: SENTINEL_VALUE}
    fork_config["playlist_config"]["playlists"][0]["plugins"][0]["plugin_settings"] = secret_settings
    instance_named(fork_config, "Meteo")["plugin_settings"][KEPT_SENTINEL_KEY] = KEPT_SENTINEL_VALUE

    exit_code, stdout, stderr, written = run_cli(config=fork_config)

    assert exit_code == 0
    for secret in (SENTINEL_KEY, SENTINEL_VALUE):
        assert secret not in stdout + stderr + written
    for kept_secret in (KEPT_SENTINEL_KEY, KEPT_SENTINEL_VALUE):
        assert kept_secret not in stdout + stderr
        assert kept_secret in written


@pytest.mark.parametrize("plugins_dir_kind", ["missing", "empty"])
def test_cli_refuses_a_plugins_dir_that_would_remove_every_instance(run_cli, tmp_path, plugins_dir_kind):
    plugins_dir = tmp_path / "plugins"
    if plugins_dir_kind == "empty":
        plugins_dir.mkdir()

    exit_code, stdout, stderr, written = run_cli(plugins_dir=plugins_dir)

    assert exit_code == 2
    assert written is None
    assert "refused" in stderr
    assert "removed" not in stdout


def test_cli_refuses_to_write_over_a_hard_link_of_its_input(run_cli, migration, fork_config, tmp_path):
    run_cli.input_copy.write_text(json.dumps(fork_config))
    hard_link = tmp_path / "hard_link.json"
    os.link(run_cli.input_copy, hard_link)
    sha256_before = hashlib.sha256(run_cli.input_copy.read_bytes()).hexdigest()

    exit_code = migration.main(
        ["--in", str(run_cli.input_copy), "--out", str(hard_link), "--display-type", "inky", "--plugins-dir", str(PLUGINS_DIR)]
    )

    assert exit_code == 2
    assert hashlib.sha256(run_cli.input_copy.read_bytes()).hexdigest() == sha256_before


@pytest.mark.parametrize("display_type", ["lcd", ""])
def test_cli_refuses_an_unsupported_display_type(run_cli, display_type):
    exit_code, _, stderr, written = run_cli(display_type=display_type)

    assert exit_code == 2
    assert "display" in stderr
    assert written is None


@pytest.mark.parametrize("interval_seconds", ["0", "86401", "-5"])
def test_cli_refuses_an_unsupported_cycle_interval(run_cli, interval_seconds):
    exit_code, _, stderr, written = run_cli("--cycle-interval-seconds", interval_seconds)

    assert exit_code == 2
    assert "interval" in stderr
    assert written is None


def corrupt(path_to_value, replacement):
    def apply(config):
        container = config
        for step in path_to_value[:-1]:
            container = container[step]
        if replacement is REMOVE:
            del container[path_to_value[-1]]
        else:
            container[path_to_value[-1]] = replacement
        return config

    return apply


REMOVE = object()
FIRST_PLAYLIST = ("playlist_config", "playlists", 0)
METEO_INSTANCE = FIRST_PLAYLIST + ("plugins", 1)
PATHOLOGICAL_CONFIGS = {
    "config is a list": lambda config: [config],
    "config is null": lambda config: None,
    "playlist_config is null": corrupt(("playlist_config",), None),
    "playlist_config is a list": corrupt(("playlist_config",), [SENTINEL_VALUE]),
    "playlists is null": corrupt(("playlist_config", "playlists"), None),
    "playlist without plugins": corrupt(FIRST_PLAYLIST + ("plugins",), REMOVE),
    "plugins is null": corrupt(FIRST_PLAYLIST + ("plugins",), None),
    "instance without plugin_id": corrupt(METEO_INSTANCE + ("plugin_id",), REMOVE),
    "plugin_id is null": corrupt(METEO_INSTANCE + ("plugin_id",), None),
    "plugin_settings is null": corrupt(METEO_INSTANCE + ("plugin_settings",), None),
    "plugin_settings is a list": corrupt(METEO_INSTANCE + ("plugin_settings",), [SENTINEL_VALUE]),
    "refresh is null": corrupt(METEO_INSTANCE + ("refresh",), None),
    "refresh is a list": corrupt(METEO_INSTANCE + ("refresh",), [SENTINEL_VALUE]),
}


@pytest.mark.parametrize("break_config", PATHOLOGICAL_CONFIGS.values(), ids=PATHOLOGICAL_CONFIGS.keys())
def test_cli_refuses_a_malformed_config_cleanly(run_cli, fork_config, break_config):
    exit_code, stdout, stderr, written = run_cli(config=break_config(fork_config))

    assert exit_code == 2
    assert written is None
    assert stderr.startswith("refused: ")
    assert SENTINEL_VALUE not in stdout + stderr


def test_cli_refuses_an_input_that_is_not_json(run_cli):
    exit_code, stdout, stderr, written = run_cli(raw_input_text=f"{{ not json {SENTINEL_VALUE}")

    assert exit_code == 2
    assert written is None
    assert stderr.startswith("refused: ")
    assert SENTINEL_VALUE not in stdout + stderr


def test_cli_leaves_the_previous_output_untouched_when_the_final_swap_fails(run_cli, monkeypatch, tmp_path):
    run_cli.output_file.write_text("previous output")

    def failing_replace(source, destination):
        raise OSError("disk detached")

    monkeypatch.setattr(os, "replace", failing_replace)

    exit_code, _, stderr, _ = run_cli()

    assert exit_code == 2
    assert stderr.startswith("refused: ")
    assert run_cli.output_file.read_text() == "previous output"
    assert [path.name for path in tmp_path.glob("*.tmp")] == []


def test_cli_leaves_no_temporary_file_after_a_successful_write(run_cli, tmp_path):
    exit_code, _, _, _ = run_cli()

    assert exit_code == 0
    assert [path.name for path in tmp_path.glob("*.tmp")] == []
