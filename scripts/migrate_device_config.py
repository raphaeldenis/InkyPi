"""Migrates a fork-shaped device.json copy to the upstream schema.

Usage:
  migrate_device_config.py --in <copy> --out <file> --display-type <type>
                           [--cycle-interval-seconds <1..86400>]
                           [--plugins-dir <upstream src/plugins>] [--no-randomize]

--in                      copy of the device.json to read; it is only read.
--out                     file to write; it must be a different file than --in and is replaced atomically.
--display-type            inky, mock, or a Waveshare model such as epd7in3e.
--cycle-interval-seconds  playlist cycle interval, 1800 by default.
--plugins-dir             upstream plugins folder; instances of plugins without a plugin-info.json there are removed.
--no-randomize            leaves image_upload instances sequential.

The report lists the names of the removed instances and the kept / removed counts, never any setting.
Exit codes: 0 migrated, 2 refused (the message names the problem, never the config content).
"""
import argparse
import copy
import fnmatch
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

DEFAULT_CYCLE_INTERVAL_SECONDS = 1800
MIN_CYCLE_INTERVAL_SECONDS = 1
MAX_CYCLE_INTERVAL_SECONDS = 86400
DEFAULT_INSTANCE_INTERVALS_SECONDS = {
    "weather": 3600,
    "calendar": 10800,
    "image_upload": 600,
    "image_folder": 600,
}
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
FIXED_DISPLAY_TYPES = ("inky", "mock")
WAVESHARE_DISPLAY_TYPE_PATTERN = "epd*in*"
UNNAMED_INSTANCE = "(unnamed)"
DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "src" / "plugins"


class MigrationRefused(ValueError):
    """Raised with a message that names the problem and never quotes the config."""


@dataclass(frozen=True)
class MigrationOptions:
    display_type: str
    known_plugin_ids: frozenset
    plugin_cycle_interval_seconds: int = DEFAULT_CYCLE_INTERVAL_SECONDS
    instance_intervals_seconds: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_INSTANCE_INTERVALS_SECONDS))
    randomize_photos: bool = True
    weather_language: str = "fr"


@dataclass(frozen=True)
class MigrationResult:
    config: dict
    removed_instance_names: tuple
    kept_instance_count: int


def discover_known_plugin_ids(plugins_dir: Path) -> frozenset:
    """Returns the ids of the plugin folders that carry a plugin-info.json."""
    if not Path(plugins_dir).is_dir():
        raise MigrationRefused("the --plugins-dir folder does not exist")
    plugin_info_files = Path(plugins_dir).glob("*/plugin-info.json")
    return frozenset(info_file.parent.name for info_file in plugin_info_files)


def is_supported_display_type(display_type) -> bool:
    if not isinstance(display_type, str):
        return False
    return display_type in FIXED_DISPLAY_TYPES or fnmatch.fnmatch(display_type, WAVESHARE_DISPLAY_TYPE_PATTERN)


def is_supported_cycle_interval(interval_seconds) -> bool:
    is_integer = isinstance(interval_seconds, int) and not isinstance(interval_seconds, bool)
    return is_integer and MIN_CYCLE_INTERVAL_SECONDS <= interval_seconds <= MAX_CYCLE_INTERVAL_SECONDS


def refuse_unusable_options(options: MigrationOptions) -> None:
    if not options.known_plugin_ids:
        raise MigrationRefused("no plugin found in --plugins-dir (expected */plugin-info.json): every instance would be removed")
    if not is_supported_display_type(options.display_type):
        raise MigrationRefused("unsupported display type: expected inky, mock or a Waveshare epd...in... model")
    if not is_supported_cycle_interval(options.plugin_cycle_interval_seconds):
        raise MigrationRefused(
            f"unsupported cycle interval: expected an integer from {MIN_CYCLE_INTERVAL_SECONDS} to {MAX_CYCLE_INTERVAL_SECONDS} seconds"
        )


def require_object(value, location: str) -> dict:
    if not isinstance(value, dict):
        raise MigrationRefused(f"{location} must be an object")
    return value


def require_list(value, location: str) -> list:
    if not isinstance(value, list):
        raise MigrationRefused(f"{location} must be a list")
    return value


def migrate_device_config_with_report(config: dict, options: MigrationOptions) -> MigrationResult:
    """Returns the upstream-shaped copy of the config and the names of the instances it dropped."""
    refuse_unusable_options(options)
    migrated = copy.deepcopy(require_object(config, "config"))
    migrated.setdefault("display_type", options.display_type)
    migrated.setdefault("inverted_image", False)
    migrated["plugin_cycle_interval_seconds"] = options.plugin_cycle_interval_seconds

    removed_instance_names = []
    kept_instance_count = 0
    if "playlist_config" in migrated:
        playlist_config = require_object(migrated["playlist_config"], "playlist_config")
        playlists = require_list(playlist_config.get("playlists"), "playlist_config.playlists")
        for playlist_index, playlist in enumerate(playlists):
            playlist_location = f"playlist_config.playlists[{playlist_index}]"
            instances = require_list(require_object(playlist, playlist_location).get("plugins"), f"{playlist_location}.plugins")
            kept_instances = []
            for instance_index, instance in enumerate(instances):
                instance_location = f"{playlist_location}.plugins[{instance_index}]"
                if is_instance_of_unknown_plugin(instance, options, instance_location):
                    removed_instance_names.append(name_of(instance))
                    continue
                migrate_instance(instance, options, instance_location)
                kept_instances.append(instance)
            playlist["plugins"] = kept_instances
            kept_instance_count += len(kept_instances)

    return MigrationResult(
        config=migrated,
        removed_instance_names=tuple(removed_instance_names),
        kept_instance_count=kept_instance_count,
    )


def migrate_device_config(config: dict, options: MigrationOptions) -> dict:
    """Returns the upstream-shaped copy of the config."""
    return migrate_device_config_with_report(config, options).config


def is_instance_of_unknown_plugin(instance, options: MigrationOptions, location: str) -> bool:
    plugin_id = require_object(instance, location).get("plugin_id")
    if not isinstance(plugin_id, str):
        raise MigrationRefused(f"{location}.plugin_id must be a string")
    return plugin_id not in options.known_plugin_ids


def name_of(instance: dict) -> str:
    name = instance.get("name")
    return name if isinstance(name, str) else UNNAMED_INSTANCE


def migrate_instance(instance: dict, options: MigrationOptions, location: str) -> None:
    """Applies the per-plugin rules to one playlist instance, in place."""
    for object_key in ("plugin_settings", "refresh"):
        if object_key in instance:
            require_object(instance[object_key], f"{location}.{object_key}")

    plugin_id = instance["plugin_id"]
    apply_refresh_interval(instance, plugin_id, options)
    if plugin_id == "weather":
        apply_weather_rules(instance, options)
    if plugin_id == "image_upload":
        apply_image_upload_rules(instance, options)


def apply_refresh_interval(instance: dict, plugin_id: str, options: MigrationOptions) -> None:
    interval_seconds = options.instance_intervals_seconds.get(plugin_id)
    if interval_seconds is None:
        return
    instance["refresh"] = {**instance.get("refresh", {}), "interval": interval_seconds}


def apply_weather_rules(instance: dict, options: MigrationOptions) -> None:
    instance.setdefault("plugin_settings", {}).setdefault("language", options.weather_language)


def apply_image_upload_rules(instance: dict, options: MigrationOptions) -> None:
    settings = instance.setdefault("plugin_settings", {})
    for fork_key in FORK_IMAGE_UPLOAD_KEYS:
        settings.pop(fork_key, None)
    if options.randomize_photos:
        settings["randomize"] = "true"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="input_path", required=True, type=Path, help="copy of the device.json to read")
    parser.add_argument("--out", dest="output_path", required=True, type=Path, help="file to write, never the input")
    parser.add_argument("--display-type", required=True, help="display type for this device, e.g. inky")
    parser.add_argument("--cycle-interval-seconds", type=int, default=DEFAULT_CYCLE_INTERVAL_SECONDS)
    parser.add_argument("--plugins-dir", type=Path, default=DEFAULT_PLUGINS_DIR)
    parser.add_argument("--no-randomize", action="store_true", help="leave photo instances sequential")
    return parser


def refuse_to_overwrite_input(input_path: Path, output_path: Path) -> None:
    output_exists = output_path.exists()
    if output_exists and input_path.exists() and os.path.samefile(input_path, output_path):
        raise MigrationRefused("--out must differ from --in")


def read_config(input_path: Path):
    try:
        return json.loads(input_path.read_text())
    except OSError:
        raise MigrationRefused("cannot read --in") from None
    except ValueError:
        raise MigrationRefused("--in is not valid JSON") from None


def write_atomically(output_path: Path, config: dict) -> None:
    """Writes the config next to its destination, then swaps it in with os.replace."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(json.dumps(config, indent=4) + "\n")
        os.replace(temporary_path, output_path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise MigrationRefused("cannot write --out") from None


def print_report(result: MigrationResult) -> None:
    for removed_name in result.removed_instance_names:
        print(f"removed instance of an unsupported plugin: {removed_name}")
    print(f"kept {result.kept_instance_count} / removed {len(result.removed_instance_names)}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs the migration from the command line and prints the migration report."""
    arguments = build_argument_parser().parse_args(argv)
    try:
        refuse_to_overwrite_input(arguments.input_path, arguments.output_path)
        options = MigrationOptions(
            display_type=arguments.display_type,
            known_plugin_ids=discover_known_plugin_ids(arguments.plugins_dir),
            plugin_cycle_interval_seconds=arguments.cycle_interval_seconds,
            randomize_photos=not arguments.no_randomize,
        )
        result = migrate_device_config_with_report(read_config(arguments.input_path), options)
        write_atomically(arguments.output_path, result.config)
    except MigrationRefused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
