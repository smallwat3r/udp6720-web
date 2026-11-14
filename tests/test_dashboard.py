import time

import pytest
from fastapi.testclient import TestClient

from udp6720.device import PSU, PSUError
from udp6720.mock import MockTransport
from udp6720.presets import PresetStore
from udp6720.transport import TransportError
from udp6720.web import create_app, load_config


@pytest.fixture
def mock():
    return MockTransport(load_ohms=10.0)


@pytest.fixture
def store(tmp_path):
    return PresetStore(tmp_path / "presets.json")


@pytest.fixture
def client(mock, store):
    app = create_app(PSU(mock, max_volts=30.0), store, poll_hz=10, extra_hosts=("testserver",))
    with TestClient(app) as c:
        yield c


def test_setpoints_are_validated_and_read_back(mock):
    psu = PSU(mock, max_volts=30.0, max_amps=2.0)
    assert psu.set_voltage(12.0) == 12.0
    assert psu.set_current(0.5) == 0.5
    for bad in (30.01, -1.0, float("nan"), float("inf")):
        with pytest.raises(PSUError):
            psu.set_voltage(bad)
    with pytest.raises(PSUError):
        psu.set_current(2.5)
    assert mock.volts == 12.0


def test_readback_mismatch_is_an_error(mock):
    mock.write = lambda command: None  # a device that ignores writes
    with pytest.raises(PSUError):
        PSU(mock).set_voltage(12.0)
    with pytest.raises(PSUError):
        PSU(mock).output(True)


def test_state_parses_measurements_and_mode(mock):
    psu = PSU(mock)
    psu.set_voltage(12.0)
    psu.set_current(0.5)
    psu.output(True)
    state = psu.read_state()
    assert (state.volts, state.amps, state.mode, state.output) == (5.0, 0.5, "CC", True)


def test_startup_never_changes_the_output(mock, client):
    assert client.get("/api/state").json()["state"]["output"] is False
    assert mock.on is False


def test_safe_start_forces_output_off(mock, store):
    mock.on = True
    with TestClient(create_app(PSU(mock), store, poll_hz=10, safe_start=True)):
        time.sleep(0.3)
        assert mock.on is False


def test_safe_start_waits_for_a_supply_that_is_off(mock, store):
    mock.on = True
    answer = mock.query
    mock.query = lambda command: (_ for _ in ()).throw(TransportError("supply is off"))
    with TestClient(create_app(PSU(mock), store, poll_hz=10, safe_start=True, extra_hosts=("testserver",))) as client:
        time.sleep(0.3)
        assert client.get("/api/state").json()["connected"] is False and mock.on is True
        mock.query = answer  # the supply is switched on
        time.sleep(0.3)
        assert client.get("/api/state").json()["connected"] is True and mock.on is False


def test_api_controls_the_supply(mock, client):
    body = client.post("/api/setpoints", json={"volts": 12, "amps": 2}).json()
    assert (body["state"]["set_volts"], body["state"]["set_amps"]) == (12.0, 2.0)
    body = client.post("/api/output", json={"on": True}).json()
    assert body["state"]["output"] is True and body["state"]["volts"] == 12.0
    assert "12.0,1.2" in client.get("/api/history.csv").text


def test_api_refuses_values_above_the_soft_limit(mock, client):
    assert client.post("/api/setpoints", json={"volts": 31}).status_code == 422
    assert client.post("/api/setpoints", json={"volts": 61}).status_code == 422
    assert mock.volts == 5.0


def test_cross_site_and_rebinding_requests_are_refused(mock, client):
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/output", json={"on": True}, headers=evil).status_code == 403
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/state", headers={"Host": "192.168.1.20:6720"}).status_code == 200
    for name in ("udp6720.localhost", "udp6720.local", "anything.localhost:6720"):
        assert client.get("/api/state", headers={"Host": name}).status_code == 200
    assert client.get("/api/state", headers={"Host": "udp6720.localhost.evil.example"}).status_code == 403
    assert mock.on is False


def test_presets_are_saved_replaced_deleted_and_persisted(client, store):
    labels = [p["label"] for p in client.get("/api/presets").json()]
    assert "Raspberry Pi" in labels
    new = {"label": "  Bench motor ", "volts": 12, "amps": 2}
    assert client.post("/api/presets", json=new).json()[-1]["label"] == "Bench motor"
    client.post("/api/presets", json={"label": "Bench motor", "volts": 6, "amps": 1})
    reloaded = PresetStore(store.path).all()
    assert len(reloaded) == len(labels) + 1 and reloaded[-1].volts == 6
    assert client.delete("/api/presets", params={"label": "Bench motor"}).status_code == 200
    assert client.delete("/api/presets", params={"label": "Bench motor"}).status_code == 404
    assert len(PresetStore(store.path).all()) == len(labels)


def test_page_and_assets_are_served_without_inline_code(client):
    page = client.get("/")
    assert "unsafe" not in page.headers["content-security-policy"]
    assert "<style" not in page.text and "onclick" not in page.text
    for asset in ("app.js", "chart.js", "components.js", "app.css", "vendor/preact-htm-3.1.1.js"):
        assert client.get(f"/static/{asset}").status_code == 200


def test_oversized_and_malformed_requests_are_refused(mock, client):
    big = {"label": "x", "volts": 1, "amps": 1, "padding": "x" * 5000}
    assert client.post("/api/presets", json=big).status_code == 413
    assert client.get("/api/state", headers={"Host": "[::1"}).status_code == 403


def test_a_polling_bug_is_reported_not_shown_as_live(mock, client):
    mock.query = lambda command: 1 / 0
    time.sleep(0.3)  # a few poll periods at 10 Hz
    assert client.get("/api/state").json()["connected"] is False
    mock.__dict__.pop("query")
    time.sleep(0.3)
    assert client.get("/api/state").json()["connected"] is True


def test_duplicate_preset_labels_in_the_file_are_refused(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text('[{"label": "a", "volts": 1, "amps": 1}, {"label": "a", "volts": 2, "amps": 1}]')
    with pytest.raises(ValueError):
        PresetStore(path)


def test_presets_can_be_reordered(client, store):
    labels = [p["label"] for p in client.get("/api/presets").json()]
    move = lambda label, offset: client.post("/api/presets/move", json={"label": label, "offset": offset})
    assert [p["label"] for p in move(labels[2], -1).json()][:3] == [labels[0], labels[2], labels[1]]
    assert move(labels[2], -5).json()[0]["label"] == labels[2]
    assert move(labels[2], 50).json()[-1]["label"] == labels[2]
    assert PresetStore(store.path).all()[-1].label == labels[2]
    assert move("nope", 1).status_code == 404


def test_invalid_presets_are_refused(client):
    for bad in (
        {"label": " ", "volts": 5, "amps": 1},
        {"label": "x", "volts": 61, "amps": 1},
        {"label": "x" * 41, "volts": 5, "amps": 1},
    ):
        assert client.post("/api/presets", json=bad).status_code == 422


def test_config_file_and_flags_are_merged_and_type_checked(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("baud = 9600\npoll_hz = 5\nmax_volts = 30.0\nsafe_start = true\n")
    config = load_config(["--poll-hz", "1", "--port", "/dev/ttyUSB1"], path)
    assert (config.baud, config.poll_hz, config.max_volts, config.safe_start) == (9600, 1.0, 30.0, True)
    assert (config.port, str(config.host), config.http_port) == ("/dev/ttyUSB1", "127.0.0.1", 6720)
    assert load_config([], tmp_path / "missing.toml").baud == "auto"

    bad_files = ('poll_hz = "fast"', "max_volts = 61", "http_port = 0", "typo_key = 1", "safe_start = 3", "not toml [")
    for text in bad_files:
        path.write_text(text)
        with pytest.raises(SystemExit):
            load_config([], path)
    path.write_text("")
    for flags in (
        ["--baud", "fast"],
        ["--poll-hz", "50"],
        ["--max-amps", "-1"],
        ["--host", "not an address"],
    ):
        with pytest.raises(SystemExit):
            load_config(flags, path)
    assert "poll_hz" in capsys.readouterr().err
