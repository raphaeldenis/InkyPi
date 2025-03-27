#!/usr/bin/env python3
"""
Setup script for running InkyPi with mock display
"""
import os
import json
import shutil
import sys

# Get the base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, 'src')

# Required directories
CONFIG_DIR = os.path.join(SRC_DIR, 'config')
STATIC_IMAGES_DIR = os.path.join(SRC_DIR, 'static', 'images')
PLUGIN_IMAGES_DIR = os.path.join(STATIC_IMAGES_DIR, 'plugins')

# Ensure directories exist
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(STATIC_IMAGES_DIR, exist_ok=True)
os.makedirs(PLUGIN_IMAGES_DIR, exist_ok=True)

# Copy config files
print("Setting up configuration files...")
device_json_src = os.path.join(BASE_DIR, 'install', 'config_base', 'device.json')
device_json_dest = os.path.join(CONFIG_DIR, 'device.json')

if not os.path.exists(device_json_dest):
    shutil.copy(device_json_src, device_json_dest)
    print(f"Copied device.json to {device_json_dest}")
else:
    print(f"Config file {device_json_dest} already exists, skipping")

# Create or update plugins.json in src/plugins
plugins_json_path = os.path.join(SRC_DIR, 'plugins', 'plugins.json')
if os.path.exists(plugins_json_path):
    print(f"Plugins config file {plugins_json_path} already exists, skipping")
else:
    # Basic plugin configuration
    plugins_config = [
        {
            "display_name": "Clock",
            "id": "clock",
            "class": "Clock"
        },
        {
            "display_name": "Image Upload",
            "id": "image_upload", 
            "class": "ImageUpload"
        }
    ]
    
    # Make sure the plugins directory exists
    os.makedirs(os.path.dirname(plugins_json_path), exist_ok=True)
    
    # Write the plugins.json file
    with open(plugins_json_path, 'w') as f:
        json.dump(plugins_config, f, indent=4)
    print(f"Created plugins config at {plugins_json_path}")

print("\nSetup complete! You can now run the app with the mock display using:")
print("python run_with_mock.py") 