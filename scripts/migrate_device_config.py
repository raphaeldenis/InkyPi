"""Migrates a fork-shaped device.json copy to the upstream schema.

Usage: migrate_device_config.py --in <copy> --out <file> --display-type <type>
The input is only read; the migrated config is always written to a different file.
"""
import argparse
import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_CYCLE_INTERVAL_SECONDS = 1800
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
DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "src" / "plugins"


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


def discover_known_plugin_ids(plugins_dir: Path) -> frozenset:
    """Returns the ids of the plugin folders that carry a plugin-info.json."""
    plugin_info_files = Path(plugins_dir).glob("*/plugin-info.json")
    return frozenset(info_file.parent.name for info_file in plugin_info_files)


def migrate_device_config_with_report(config: dict, options: MigrationOptions) -> MigrationResult:
    """Returns the upstream-shaped copy of the config and the names of the instances it dropped."""
    migrated = copy.deepcopy(config)
    migrated.setdefault("display_type", options.display_type)
    migrated.setdefault("inverted_image", False)
    migrated["plugin_cycle_interval_seconds"] = options.plugin_cycle_interval_seconds

    removed_instance_names = []
    for playlist in migrated.get("playlist_config", {}).get("playlists", []):
        kept_instances = []
        for instance in playlist["plugins"]:
            if instance["plugin_id"] not in options.known_plugin_ids:
                removed_instance_names.append(instance["name"])
                continue
            migrate_instance(instance, options)
            kept_instances.append(instance)
        playlist["plugins"] = kept_instances

    return MigrationResult(config=migrated, removed_instance_names=tuple(removed_instance_names))


def migrate_device_config(config: dict, options: MigrationOptions) -> dict:
    """Returns the upstream-shaped copy of the config."""
    return migrate_device_config_with_report(config, options).config


def migrate_instance(instance: dict, options: MigrationOptions) -> None:
    """Applies the per-plugin rules to one playlist instance, in place."""
    plugin_id = instance["plugin_id"]
    settings = instance.setdefault("plugin_settings", {})

    interval_seconds = options.instance_intervals_seconds.get(plugin_id)
    if interval_seconds is not None:
        instance["refresh"] = {**instance.get("refresh", {}), "interval": interval_seconds}

    if plugin_id == "weather":
        settings.setdefault("language", options.weather_language)

    if plugin_id == "image_upload":
        for fork_key in FORK_IMAGE_UPLOAD_KEYS:
            settings.pop(fork_key, None)
        if options.randomize_photos:
            settings["randomize"] = "true"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input_path", required=True, type=Path, help="copy of the device.json to read")
    parser.add_argument("--out", dest="output_path", required=True, type=Path, help="file to write, never the input")
    parser.add_argument("--display-type", required=True, help="display type for this device, e.g. inky")
    parser.add_argument("--cycle-interval-seconds", type=int, default=DEFAULT_CYCLE_INTERVAL_SECONDS)
    parser.add_argument("--plugins-dir", type=Path, default=DEFAULT_PLUGINS_DIR)
    parser.add_argument("--no-randomize", action="store_true", help="leave photo instances sequential")
    return parser


def main(argv: Sequence[str] = None) -> int:
    """Runs the migration from the command line and prints the migration report."""
    arguments = build_argument_parser().parse_args(argv)

    writes_over_input = arguments.output_path.resolve() == arguments.input_path.resolve()
    if writes_over_input:
        print("refused: --out must differ from --in", file=sys.stderr)
        return 2

    options = MigrationOptions(
        display_type=arguments.display_type,
        known_plugin_ids=discover_known_plugin_ids(arguments.plugins_dir),
        plugin_cycle_interval_seconds=arguments.cycle_interval_seconds,
        randomize_photos=not arguments.no_randomize,
    )
    result = migrate_device_config_with_report(json.loads(arguments.input_path.read_text()), options)
    arguments.output_path.write_text(json.dumps(result.config, indent=4) + "\n")

    for removed_name in result.removed_instance_names:
        print(f"removed instance of an unsupported plugin: {removed_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
