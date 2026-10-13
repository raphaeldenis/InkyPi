import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from conftest import REPO_ROOT

TEST_THAT_SAVES_SETTINGS = (
    "tests/test_plugin_settings_api.py::test_user_saves_calendar_settings_without_url_being_persisted"
)
EMPTY_CALENDAR = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


class RecordingCalendarServer:
    """Real loopback HTTP server that counts the requests it receives."""

    def __init__(self):
        self.request_count = 0
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                recorder.request_count += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(EMPTY_CALENDAR)

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_port}/calendar.ics"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def test_suite_makes_no_network_call_when_calendar_url_is_exported_in_the_shell():
    server = RecordingCalendarServer()
    shell_environment = {**os.environ, "CALENDAR_URL": server.url}

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_THAT_SAVES_SETTINGS, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=shell_environment,
        capture_output=True,
        check=False,
        text=True,
    )
    server.close()

    assert completed.returncode == 0, completed.stdout
    assert server.request_count == 0
