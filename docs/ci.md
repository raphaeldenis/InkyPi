# Continuous integration

The workflow `.github/workflows/ci.yml` runs on `push` and `pull_request` to `main`. It has one job, `checks`, which uses no secret and never reaches the Raspberry Pi.

## What the CI covers

- Syntax errors and undefined names, via `ruff` rules E9, F63, F7 and F82.
- The `pytest` tests in `tests/`.

The tests use no network, no `.env` and no real `device.json` (see `tests/conftest.py`), but the suite has no global network block.

## What the CI does not cover

Inky/Waveshare display output, Chromium rendering, systemd and the install scripts, ARM/Pi behavior (the job runs on `ubuntu-latest`, x86-64), and third-party APIs.

## Reproduce locally

The job runs Python 3.11. That version is a choice: the repository does not document the Python version on the Pi, so it needs confirming.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r install/requirements-dev.txt ruff==0.8.6
ruff check --select E9,F63,F7,F82 \
  --per-file-ignores 'src/blueprints/plugin.py:F821' \
  --per-file-ignores 'src/utils/app_utils.py:F821' \
  .
pytest -q
```

## Known undefined names

| File | Name |
| --- | --- |
| `src/blueprints/plugin.py` | `plugin_instance_name` |
| `src/utils/app_utils.py` | `subprocess` |

The F821 exclusion covers these two files entirely, so a new undefined name in them goes undetected while these names stay undefined. The code is unchanged here, and the exclusions stay while these names stay undefined.

Deleting `.github/workflows/ci.yml` removes the CI.
