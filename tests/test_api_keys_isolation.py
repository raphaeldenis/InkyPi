import pytest

FAKE_KEY_NAME = "FAKE_SERVICE_KEY"
FAKE_KEY_VALUE = "not-a-real-secret"


@pytest.fixture
def client(make_app):
    return make_app().test_client()


@pytest.fixture(autouse=True)
def restore_environment_after_save(monkeypatch):
    monkeypatch.setenv(FAKE_KEY_NAME, "")


def test_listing_api_keys_reads_the_disposable_dotenv_and_never_opens_the_real_one(
    client, disposable_dotenv, real_files_access
):
    disposable_dotenv.write_text(f"{FAKE_KEY_NAME}={FAKE_KEY_VALUE}\n")

    response = client.get("/api-keys")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert FAKE_KEY_NAME in page
    assert FAKE_KEY_VALUE not in page
    assert real_files_access.attempts == []


@pytest.mark.parametrize(
    "entries",
    [[], [{"key": FAKE_KEY_NAME, "value": FAKE_KEY_VALUE}]],
    ids=["empty entries", "one fake entry"],
)
def test_saving_api_keys_writes_the_disposable_dotenv_and_never_the_real_one(
    client, disposable_dotenv, real_files_access, entries
):
    response = client.post("/api-keys/save", json={"entries": entries})

    saved_dotenv = disposable_dotenv.read_text()
    assert response.status_code == 200
    assert saved_dotenv.count("=") == len(entries)
    assert real_files_access.attempts == []
