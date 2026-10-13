import shutil
import subprocess

import pytest

from tests.conftest import REPO_ROOT

CURRENT_IMAGE = "src/static/images/current_image.png"
PLUGIN_IMAGE = "src/static/images/plugins/some_plugin/render.png"
PLUGIN_IMAGES_OWN_GITIGNORE = "src/static/images/plugins/.gitignore"

is_git_checkout = shutil.which("git") is not None and (REPO_ROOT / ".git").exists()

pytestmark = pytest.mark.skipif(not is_git_checkout, reason="needs a git checkout")


def is_ignored_by_git(relative_path):
    result = subprocess.run(
        ["git", "check-ignore", "-q", relative_path], cwd=REPO_ROOT, capture_output=True
    )
    return result.returncode == 0


def test_current_image_written_by_the_app_is_ignored():
    assert is_ignored_by_git(CURRENT_IMAGE)


def test_plugin_images_stay_ignored_by_their_own_gitignore():
    assert is_ignored_by_git(PLUGIN_IMAGE)
    assert not is_ignored_by_git(PLUGIN_IMAGES_OWN_GITIGNORE)
