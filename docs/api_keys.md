
# Storing API Keys

Certain plugins, like the AI Image plugin, require API credentials to function. These credentials must be stored in a .env file located at the root of the project. Once you have your API token, follow these steps:

1. SSH into your Raspberry Pi and navigate to the InkyPi directory:
    ```bash
    cd InkyPi
    ```
2. Create or edit the .env file using your preferred text editor (e.g., vi, nano):
    ```bash
    vi .env
    ```
3. Add your API keys following format, with one line per key:
    ```
    PLUGIN_KEY=your-key
    ```
4. Save the file and exit the editor

## Open AI Key

Required for the AI Image and AI Text Plugins

- Login or create an account on the [Open AI developer platform](https://platform.openai.com/docs/overview)
- Crate a secret key from the API Keys tab in the Settings page
    - It is recommended to set up Auto recharge (found in the "Billing" tab)
    - Optionally set a Budge Limit in the Limits tab
- Store your key in the .env file with the key OPEN_AI_SECRET
    ```
    OPEN_AI_SECRET=your-key
    ```

## Calendar URL

Required for the Calendar (iCalendar) Plugin

- Copy the iCalendar link from your calendar provider (Google Calendar, Apple Calendar, Outlook, etc.). Both `https://` and `webcal://` links are supported.
- Store it in the .env file with the key CALENDAR_URL. It is never stored in `device.json` and must never be committed.
    ```
    CALENDAR_URL=your-calendar-link
    ```
- Without it, the calendar renders with no events.

## Development setup

A fresh clone has no `src/config/device.json` (it is not tracked). Create it from the minimal base config, or start from `src/config/device.example.json`:

```bash
cp install/config_base/device.json src/config/device.json
```

## Open Weather Map

Required for the Weather Plugin

- Login or create an account on [OpenWeatherMap](https://home.openweathermap.org/users/sign_in)
    - Verify your email after signing up
- The weather plugin uses the [One Call API 3.0](https://openweathermap.org/price) which requires a subscription but is free for up to 1,000 requests per day.
    - Subscribe at [One Call API 3.0 Subscription](https://home.openweathermap.org/subscriptions/billing_info/onecall_30/base?key=base&service=onecall_30)
    - Follow the instructions to complete the subscription.
    - Navigate to [Your Subscriptions](https://home.openweathermap.org/subscriptions) and set "Calls per day (no more than)" to 1,000 to avoid exceeding the free limit
- Store your api key in the .env file with the key OPEN_WEATHER_MAP_SECRET
    ```
    OPEN_WEATHER_MAP_SECRET=your-key
    ```