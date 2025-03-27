#!/usr/bin/env python3
"""
Script to run InkyPi with a mock display for local development.
"""
import os
import sys
import subprocess

# Run the setup script first
print("Setting up mock environment...")
setup_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'setup_mock.py')
subprocess.call([sys.executable, setup_script])

# Set environment variables
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
os.environ['INKYPI_MOCK_DISPLAY'] = 'true'
os.environ['SRC_DIR'] = src_dir
os.environ['PORT'] = '8080'  # Use port 8080 instead of 80

# Add the src directory to the Python path if not already there
if src_dir not in sys.path:
    sys.path.append(src_dir)

# Patch the app to use the PORT environment variable
inkypi_file = os.path.join(src_dir, 'inkypi.py')
with open(inkypi_file, 'r') as f:
    content = f.read()

if 'port=80' in content and 'os.getenv("PORT", 80)' not in content:
    # Update the Flask app.run call to use PORT env var
    content = content.replace('port=80', 'port=int(os.getenv("PORT", 80))')
    with open(inkypi_file, 'w') as f:
        f.write(content)
    print(f"Patched {inkypi_file} to use PORT environment variable")

print("\nStarting InkyPi with mock display...")
print("Access the web interface at http://localhost:8080/")

# Run the Flask app directly rather than importing it
inkypi_path = os.path.join(src_dir, 'inkypi.py')
# Use subprocess to run the script in a way that won't terminate when imported
os.chdir(src_dir)  # Make sure we're in the src directory when running the script
subprocess.call([sys.executable, inkypi_path]) 