import requests
import sys

# URL for plugin update
url = "http://localhost:8080/update_plugin_instance/ICLOUD"

# Data payload with required parameters
data = {
    "plugin_id": "icalendar",
    "name": "iCloud Calendar",
    "calendarUrl": "https://example.com/calendar.ics",
    "viewMode": "week",
    "daysToShow": "14",
    "maxEvents": "10",
    "colorScheme": "blue"
}

# Make the PUT request
try:
    response = requests.put(url, data=data)
    print(f"Status code: {response.status_code}")
    print(f"Response: {response.text}")
    
    if response.status_code == 200:
        print("Settings updated successfully!")
        
        # Now trigger an update
        update_url = "http://localhost:8080/display_plugin_instance"
        update_data = {
            "playlist": "Default",
            "plugin_instance": "ICLOUD"
        }
        update_response = requests.post(update_url, data=update_data)
        print(f"Update status: {update_response.status_code}")
        print(f"Update response: {update_response.text}")
    else:
        print("Failed to update settings")
except Exception as e:
    print(f"Error: {e}") 