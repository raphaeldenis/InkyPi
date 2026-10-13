"""Migrates a fork-shaped device.json copy to the upstream schema.

Reads --in, writes a different --out atomically, and reports each removed instance (playlist, plugin id, name), never a setting.
Exit codes: 0 migrated, 2 refused (the message names the problem, never the config content).
"""
import argparse
import copy
import fnmatch
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_CYCLE_INTERVAL_SECONDS = 1800
MIN_CYCLE_INTERVAL_SECONDS = 1
MAX_CYCLE_INTERVAL_SECONDS = 86400
INSTANCE_INTERVALS_SECONDS = {
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
WEATHER_LANGUAGE = "fr"
FIXED_DISPLAY_TYPES = ("inky", "mock")
WAVESHARE_DISPLAY_TYPE_PATTERN = "epd*in*"
UNNAMED_INSTANCE = "(unnamed)"
DEFAULT_REMOVABLE_PLUGIN_IDS = frozenset({"icalendar", "icloud_calendar"})
DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "src" / "plugins"


class MigrationRefused(ValueError):
    """Raised with a message that names the problem and never quotes the config."""


@dataclass(frozen=True)
class MigrationOptions:
    display_type: str
    known_plugin_ids: frozenset
    removable_plugin_ids: frozenset = DEFAULT_REMOVABLE_PLUGIN_IDS
    plugin_cycle_interval_seconds: int = DEFAULT_CYCLE_INTERVAL_SECONDS
    randomize_photos: bool = True


@dataclass(frozen=True)
class RemovedInstance:
    playlist_name: str
    plugin_id: str
    instance_name: str


@dataclass(frozen=True)
class MigrationResult:
    config: dict
    removed_instances: tuple
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


def refuse_unsupported_display_type(display_type) -> None:
    if not is_supported_display_type(display_type):
        raise MigrationRefused("unsupported display type: expected inky, mock or a Waveshare epd...in... model")


def is_supported_cycle_interval(interval_seconds) -> bool:
    is_integer = isinstance(interval_seconds, int) and not isinstance(interval_seconds, bool)
    return is_integer and MIN_CYCLE_INTERVAL_SECONDS <= interval_seconds <= MAX_CYCLE_INTERVAL_SECONDS


def refuse_unusable_options(options: MigrationOptions) -> None:
    if not options.known_plugin_ids:
        raise MigrationRefused("no plugin found in --plugins-dir (expected */plugin-info.json): every instance would be removed")
    refuse_unsupported_display_type(options.display_type)
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
    """Returns the upstream-shaped copy of the config and the instances it dropped."""
    refuse_unusable_options(options)
    migrated = copy.deepcopy(require_object(config, "config"))
    migrated.setdefault("display_type", options.display_type)
    refuse_unsupported_display_type(migrated["display_type"])
    migrated.setdefault("inverted_image", False)
    migrated["plugin_cycle_interval_seconds"] = options.plugin_cycle_interval_seconds

    removed_instances = []
    kept_instance_count = 0
    if "playlist_config" in migrated:
        playlist_config = require_object(migrated["playlist_config"], "playlist_config")
        playlists = require_list(playlist_config.get("playlists"), "playlist_config.playlists")
        for playlist_index, playlist in enumerate(playlists):
            playlist_location = f"playlist_config.playlists[{playlist_index}]"
            instances = require_list(require_object(playlist, playlist_location).get("plugins"), f"{playlist_location}.plugins")
            kept_instances = []
            kept_original_positions = []
            for instance_index, instance in enumerate(instances):
                instance_location = f"{playlist_location}.plugins[{instance_index}]"
                if is_instance_of_unknown_plugin(instance, options, instance_location):
                    removed_instances.append(describe_removed_instance(playlist, instance))
                    continue
                migrate_instance(instance, options, instance_location)
                kept_instances.append(instance)
                kept_original_positions.append(instance_index)
            if "current_plugin_index" in playlist:
                playlist["current_plugin_index"] = follow_removals(
                    playlist["current_plugin_index"],
                    original_count=len(instances),
                    kept_original_positions=kept_original_positions,
                    location=f"{playlist_location}.current_plugin_index",
                )
            playlist["plugins"] = kept_instances
            kept_instance_count += len(kept_instances)

    return MigrationResult(
        config=migrated,
        removed_instances=tuple(removed_instances),
        kept_instance_count=kept_instance_count,
    )


def migrate_device_config(config: dict, options: MigrationOptions) -> dict:
    """Returns the upstream-shaped copy of the config."""
    return migrate_device_config_with_report(config, options).config


def is_instance_of_unknown_plugin(instance, options: MigrationOptions, location: str) -> bool:
    """Returns True for an instance to remove; refuses an unknown plugin nobody allowed to remove."""
    plugin_id = require_object(instance, location).get("plugin_id")
    if not isinstance(plugin_id, str):
        raise MigrationRefused(f"{location}.plugin_id must be a string")
    if plugin_id in options.known_plugin_ids:
        return False
    if plugin_id not in options.removable_plugin_ids:
        raise MigrationRefused(
            f"{location}: plugin_id {escaped(plugin_id)} is absent from --plugins-dir and not allowed for removal (--remove-plugin-id)"
        )
    return True


def follow_removals(current_index, original_count: int, kept_original_positions: list, location: str):
    """Returns the index of the last kept instance at or before the shown one, so the next instance stays the same.

    None means no kept instance precedes the shown position: the cycle starts on the first kept instance.
    An index past the end counts as upstream counts it, modulo the original length.
    """
    if current_index is None:
        return None
    is_integer = isinstance(current_index, int) and not isinstance(current_index, bool)
    if not is_integer:
        raise MigrationRefused(f"{location} must be an integer or null")
    if original_count == 0:
        return None
    shown_position = current_index % original_count
    kept_up_to_shown = sum(1 for position in kept_original_positions if position <= shown_position)
    return kept_up_to_shown - 1 if kept_up_to_shown else None


def escaped(value) -> str:
    """Returns the value as a quoted, ASCII-only literal: no control character, no line break."""
    return json.dumps(value if isinstance(value, str) else UNNAMED_INSTANCE)


def describe_removed_instance(playlist: dict, instance: dict) -> RemovedInstance:
    return RemovedInstance(
        playlist_name=playlist.get("name"),
        plugin_id=instance["plugin_id"],
        instance_name=instance.get("name"),
    )


def migrate_instance(instance: dict, options: MigrationOptions, location: str) -> None:
    """Applies the per-plugin rules to one playlist instance, in place."""
    for object_key in ("plugin_settings", "refresh"):
        if object_key in instance:
            require_object(instance[object_key], f"{location}.{object_key}")

    plugin_id = instance["plugin_id"]
    apply_refresh_interval(instance, plugin_id)
    if plugin_id == "weather":
        apply_weather_rules(instance)
    if plugin_id == "image_upload":
        apply_image_upload_rules(instance, options)


def apply_refresh_interval(instance: dict, plugin_id: str) -> None:
    interval_seconds = INSTANCE_INTERVALS_SECONDS.get(plugin_id)
    refresh = instance.get("refresh", {})
    is_scheduled_at_a_time_of_day = "scheduled" in refresh
    if interval_seconds is None or is_scheduled_at_a_time_of_day:
        return
    instance["refresh"] = {**refresh, "interval": interval_seconds}


def apply_weather_rules(instance: dict) -> None:
    instance.setdefault("plugin_settings", {}).setdefault("language", WEATHER_LANGUAGE)


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
    parser.add_argument("--display-type", required=True, help="inky, mock, or a Waveshare model such as epd7in3e")
    parser.add_argument(
        "--cycle-interval-seconds",
        type=int,
        default=DEFAULT_CYCLE_INTERVAL_SECONDS,
        help=f"playlist cycle interval, {MIN_CYCLE_INTERVAL_SECONDS} to {MAX_CYCLE_INTERVAL_SECONDS} (default %(default)s)",
    )
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=DEFAULT_PLUGINS_DIR,
        help="upstream src/plugins folder; a plugin without a plugin-info.json there must be allowed for removal",
    )
    parser.add_argument(
        "--remove-plugin-id",
        action="append",
        default=[],
        dest="removable_plugin_ids",
        metavar="PLUGIN_ID",
        help=f"also remove the instances of this plugin id when absent from --plugins-dir (repeatable; always allowed: {', '.join(sorted(DEFAULT_REMOVABLE_PLUGIN_IDS))})",
    )
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
    for removed in result.removed_instances:
        playlist_name, plugin_id, instance_name = (
            escaped(removed.playlist_name),
            escaped(removed.plugin_id),
            escaped(removed.instance_name),
        )
        print(f"removed instance: playlist={playlist_name} plugin_id={plugin_id} name={instance_name}")
    print(f"kept {result.kept_instance_count} / removed {len(result.removed_instances)}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs the migration from the command line and prints the migration report."""
    arguments = build_argument_parser().parse_args(argv)
    try:
        refuse_to_overwrite_input(arguments.input_path, arguments.output_path)
        options = MigrationOptions(
            display_type=arguments.display_type,
            known_plugin_ids=discover_known_plugin_ids(arguments.plugins_dir),
            removable_plugin_ids=DEFAULT_REMOVABLE_PLUGIN_IDS | frozenset(arguments.removable_plugin_ids),
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
