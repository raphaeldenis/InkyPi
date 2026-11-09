# Continuous integration

One GitHub Actions workflow, `.github/workflows/ci.yml`, runs a single job named `checks`. It uses no secret and never reaches the Raspberry Pi.

## Triggers

- `push` on `main`
- `pull_request` targeting `main`

A new push to the same ref cancels the run still in progress. The job times out after 10 minutes and the token is limited to `contents: read`.

## Python version

The job runs Python 3.11. No file in this repository states the Python version installed on the Pi, so 3.11 is a choice, not a mirror of the device. Changing it means editing `python-version` in `ci.yml` and the `python3.11` command below.

## What the CI covers

- **Syntax**: `python -m compileall -q src scripts` fails on any file that does not compile.
- **Undefined names**: `ruff check --select E9,F63,F7,F82 .` reports syntax errors, invalid comparisons, and undefined names (for example a module used without being imported). No style rule is enabled.
- **pytest tests that need no hardware**: `pytest -q` runs `tests/` (`pytest.ini` sets `testpaths = tests` and `pythonpath = src`). On `main` at `d5c7267` it collects 14 tests.
- **Network**: the suite has no global network block. The calendar tests replace `urllib.request.urlopen` with the `calendar_network` fixture from `tests/conftest.py`, which raises `AssertionError("unexpected network call")` when no response is configured, and replace the Chromium subprocess with the `fake_chromium` fixture. One test (`tests/test_test_isolation.py`) starts a loopback HTTP server on `127.0.0.1` and asserts that it receives no request.
- **No `.env` or real `device.json`**: the job has neither. `tests/conftest.py` replaces `load_dotenv` and removes `CALENDAR_URL` from the environment for every test, and the Flask app fixture points `Config.config_file` at a `device.json` copied from `install/config_base/` into a temporary directory.

## Known undefined names (excluded from the lint step)

On `main` at `d5c7267`, ruff reports three real `F821` errors. The lint step excludes the rule for these two files only, with `--config 'lint.per-file-ignores = ...'` in `ci.yml`; every other rule and file stays checked.

| File | Line | Name | Reason |
| --- | --- | --- | --- |
| `src/blueprints/plugin.py` | 93 | `plugin_instance_name` | The error message uses a variable that is never defined (the variable in scope is `instance_name`). |
| `src/utils/app_utils.py` | 60, 62 | `subprocess` | `get_wifi_name` calls `subprocess` without importing it. |

The code is left unchanged here. A separate `fix/` ticket with a test removes these exclusions.

## What the CI does not cover

- Inky or Waveshare e-ink display output
- Chromium rendering
- systemd units and `install/install.sh`, `update.sh`, `uninstall.sh`
- ARM and Raspberry Pi behavior (the job runs on `ubuntu-latest`, x86-64)
- Real API keys and third-party services
- Deployment to a device

## Reproduce locally

From the repository root, in a clean virtual environment:

```bash
python3.11 -m venv /tmp/inkypi-ci-venv
source /tmp/inkypi-ci-venv/bin/activate
pip install -r install/requirements-dev.txt ruff==0.8.6
python -m compileall -q src scripts
ruff check --select E9,F63,F7,F82 \
  --config 'lint.per-file-ignores = {"src/blueprints/plugin.py" = ["F821"], "src/utils/app_utils.py" = ["F821"]}' \
  .
pytest -q
```

Each command exits with 0 when the check passes.

## Veto

The workflow is one commit. To remove the CI, revert the commit that added `.github/workflows/ci.yml`.
