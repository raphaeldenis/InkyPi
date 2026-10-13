import os
import subprocess
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from config import Config
from tests.conftest import REPO_ROOT

SECRET_SUFFIXES_THE_SUITE_MUST_HIDE = ("_KEY", "_SECRET", "_TOKEN", "_URL", "_PASSWORD")


def build_probe_test_source(probe_key):
    return f'''
import requests
from config import Config


def test_probe_key_is_invisible_to_the_suite():
    probe_url = Config.__new__(Config).load_env_key("{probe_key}")
    if probe_url:
        requests.get(probe_url, timeout=5)
    assert probe_url is None
'''


class RecordingLoopbackServer:
    """Real loopback HTTP server that counts the requests it receives."""

    def __init__(self):
        self.request_count = 0
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                recorder.request_count += 1
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_port}/probe"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def mirror_repo_with_a_dotenv_pointing_at(probe_key, probe_url, mirror_root):
    """Builds a checkout look-alike whose .env sits next to src, with the repo conftest and a probe test."""
    mirror_root.mkdir()
    (mirror_root / "src").symlink_to(REPO_ROOT / "src")
    (mirror_root / "install").symlink_to(REPO_ROOT / "install")
    (mirror_root / "pytest.ini").write_text((REPO_ROOT / "pytest.ini").read_text())
    (mirror_root / ".env").write_text(f"{probe_key}={probe_url}\n")
    mirror_tests = mirror_root / "tests"
    mirror_tests.mkdir()
    (mirror_tests / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (mirror_tests / "test_probe.py").write_text(build_probe_test_source(probe_key))


@pytest.mark.parametrize("secret_suffix", SECRET_SUFFIXES_THE_SUITE_MUST_HIDE)
def test_suite_makes_no_network_call_when_a_key_is_in_the_shell_and_in_a_dotenv(tmp_path, secret_suffix):
    probe_key = f"ISOLATION_PROBE{secret_suffix}"
    server = RecordingLoopbackServer()
    mirror_root = tmp_path / "checkout"
    mirror_repo_with_a_dotenv_pointing_at(probe_key, server.url, mirror_root)
    shell_environment = {
        **os.environ,
        probe_key: server.url,
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_probe.py", "-q", "-p", "no:cacheprovider"],
        cwd=mirror_root,
        env=shell_environment,
        capture_output=True,
        check=False,
        text=True,
    )
    server.close()

    assert completed.returncode == 0, completed.stdout
    assert server.request_count == 0


def test_image_paths_written_by_config_live_under_tmp_path(tmp_path):
    assert Path(Config.current_image_file).parent == tmp_path
    assert Path(Config.plugin_image_dir).parent == tmp_path
