import subprocess

from tests.conftest import REPO_ROOT

CURRENT_IMAGE = "src/static/images/current_image.png"


def is_ignored_by_git(relative_path):
    result = subprocess.run(
        ["git", "check-ignore", "-q", relative_path], cwd=REPO_ROOT, capture_output=True
    )
    return result.returncode == 0


def test_current_image_written_by_the_app_is_ignored():
    assert is_ignored_by_git(CURRENT_IMAGE)
