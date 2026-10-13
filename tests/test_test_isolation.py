import os
import subprocess
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from config import Config
from tests.conftest import REPO_ROOT, is_allowed_environment_name

PROBE_KEY_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_URL", "_PASSWORD", "_CREDENTIAL", "")


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


def build_key_probe_test_source(probe_key):
    return f'''
import requests
from config import Config


def test_probe_key_is_invisible_to_the_suite(outbound_network_guard):
    outbound_network_guard.allow("127.0.0.1")
    probe_url = Config.__new__(Config).load_env_key("{probe_key}")
    if probe_url:
        requests.get(probe_url, timeout=5)
    assert probe_url is None
'''


def build_blocked_network_test_source(target_url):
    return f'''
import pytest
import requests


def test_call_without_any_key_is_blocked():
    with pytest.raises(Exception, match="blocked by the test suite"):
        requests.get("{target_url}", timeout=5)
'''


def mirror_repo_with_a_dotenv_pointing_at(probe_key, probe_url, probe_test_source, mirror_root):
    """Builds a checkout look-alike whose .env sits next to src, with the repo conftest and a probe test."""
    mirror_root.mkdir()
    (mirror_root / "src").symlink_to(REPO_ROOT / "src")
    (mirror_root / "install").symlink_to(REPO_ROOT / "install")
    (mirror_root / "pytest.ini").write_text((REPO_ROOT / "pytest.ini").read_text())
    (mirror_root / ".env").write_text(f"{probe_key}={probe_url}\n")
    mirror_tests = mirror_root / "tests"
    mirror_tests.mkdir()
    (mirror_tests / "conftest.py").write_text((REPO_ROOT / "tests" / "conftest.py").read_text())
    (mirror_tests / "test_probe.py").write_text(probe_test_source)


def run_probe_suite(tmp_path, probe_key, build_probe_test_source, exported_in_shell):
    server = RecordingLoopbackServer()
    mirror_root = tmp_path / "checkout"
    mirror_repo_with_a_dotenv_pointing_at(probe_key, server.url, build_probe_test_source(server.url), mirror_root)
    shell_environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if exported_in_shell:
        shell_environment[probe_key] = server.url

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_probe.py", "-q", "-p", "no:cacheprovider"],
        cwd=mirror_root,
        env=shell_environment,
        capture_output=True,
        check=False,
        text=True,
    )
    server.close()
    return completed, server


@pytest.mark.parametrize("key_suffix", PROBE_KEY_SUFFIXES)
def test_suite_makes_no_network_call_when_a_key_is_in_the_shell_and_in_a_dotenv(tmp_path, key_suffix):
    probe_key = f"ISOLATION_PROBE{key_suffix}"

    completed, server = run_probe_suite(
        tmp_path,
        probe_key,
        lambda server_url: build_key_probe_test_source(probe_key),
        exported_in_shell=True,
    )

    assert completed.returncode == 0, completed.stdout
    assert server.request_count == 0


def test_suite_blocks_a_network_call_made_without_any_key(tmp_path):
    completed, server = run_probe_suite(
        tmp_path,
        "ISOLATION_PROBE_UNUSED",
        build_blocked_network_test_source,
        exported_in_shell=False,
    )

    assert completed.returncode == 0, completed.stdout
    assert server.request_count == 0


def test_suite_keeps_only_allowlisted_variables_in_the_environment():
    names_outside_allowlist = [name for name in os.environ if not is_allowed_environment_name(name)]

    assert names_outside_allowlist == []


def test_image_paths_written_by_config_live_under_tmp_path(tmp_path):
    assert Path(Config.current_image_file).parent == tmp_path
    assert Path(Config.plugin_image_dir).parent == tmp_path
