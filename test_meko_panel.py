from urllib.parse import quote
from unittest.mock import Mock

import config
import meko_client
import app as app_module


ASHA_ID = "test-asha"
OTHER_ASHA_ID = "other-asha"
PATIENT_PHONE = "whatsapp:+15551230000"
OTHER_PHONE = "whatsapp:+15551239999"


def _patients(asha_id):
    if asha_id == ASHA_ID:
        return {
            PATIENT_PHONE: {
                "phone": PATIENT_PHONE,
                "name": "Test Patient",
                "week": 24,
            }
        }
    if asha_id == OTHER_ASHA_ID:
        return {
            OTHER_PHONE: {
                "phone": OTHER_PHONE,
                "name": "Other Patient",
                "week": 12,
            }
        }
    return {}


def _client(monkeypatch, asha_id=ASHA_ID):
    app_module.app.config["TESTING"] = True
    monkeypatch.setattr(app_module, "get_all_patients", _patients)
    monkeypatch.setattr(app_module, "get_meko_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        app_module,
        "get_meko_event_summary",
        lambda *_args, **_kwargs: {
            "recalls": 0,
            "stores": 0,
            "corrections": 0,
            "forgets": 0,
            "failures": 0,
            "last_successful_at": None,
        },
    )
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["asha_id"] = asha_id
    return client


def test_panel_renders_off_and_does_not_request_memory(monkeypatch):
    monkeypatch.setattr(config, "MEKO_ENABLED", False)
    snapshot = Mock(side_effect=AssertionError("Meko must stay off"))
    monkeypatch.setattr(meko_client, "get_patient_memory_snapshot", snapshot)
    client = _client(monkeypatch)

    response = client.get(f"/dashboard/{ASHA_ID}/meko")
    assert response.status_code == 200
    assert b"MEKO OFF" in response.data
    assert b"Meko is OFF" in response.data
    assert b"/dashboard/test-asha" in response.data

    data = client.get(f"/dashboard/{ASHA_ID}/meko/data")
    assert data.status_code == 200
    assert data.get_json()["patients"][0]["memory"]["state"] == "off"
    snapshot.assert_not_called()


def test_data_endpoint_returns_live_recalled_fields(monkeypatch):
    monkeypatch.setattr(config, "MEKO_ENABLED", True)
    snapshot = {
        "state": "available",
        "record": {
            "level": "RED",
            "status": "active",
            "week": 24,
            "recorded_at": "2026-10-31T12:00:00+00:00",
            "age_seconds": 300,
            "memory_id": "memory-uuid",
            "raw_excerpt": "MATRISETU_TRIAGE_V1 {<script>seeded</script>}",
            "seeded": True,
            "effective_level": "RED",
        },
        "records": [],
    }
    get_snapshot = Mock(return_value=snapshot)
    monkeypatch.setattr(meko_client, "get_patient_memory_snapshot", get_snapshot)
    client = _client(monkeypatch)

    response = client.get(f"/dashboard/{ASHA_ID}/meko/data")

    assert response.status_code == 200
    patient = response.get_json()["patients"][0]
    assert patient["memory"]["record"]["level"] == "RED"
    assert patient["memory"]["record"]["memory_id"] == "memory-uuid"
    assert patient["memory"]["record"]["raw_excerpt"].startswith("MATRISETU_TRIAGE_V1")
    get_snapshot.assert_called_once_with(PATIENT_PHONE, ASHA_ID)


def test_correct_and_forget_are_scoped_to_logged_in_asha(monkeypatch):
    monkeypatch.setattr(config, "MEKO_ENABLED", True)
    correct = Mock(return_value={"ok": True, "memory_id": "memory-uuid"})
    forget = Mock(return_value={"ok": True, "deleted": 1})
    monkeypatch.setattr(meko_client, "correct_patient_triage_memory", correct)
    monkeypatch.setattr(meko_client, "forget_patient_triage_memory", forget)
    client = _client(monkeypatch)
    patient_path = quote(PATIENT_PHONE, safe="")

    corrected = client.post(
        f"/dashboard/{ASHA_ID}/meko/{patient_path}/correct",
        json={"level": "AMBER", "status": "active"},
    )
    assert corrected.status_code == 200
    correct.assert_called_once_with(PATIENT_PHONE, "AMBER", "active", ASHA_ID)

    forgotten = client.post(
        f"/dashboard/{ASHA_ID}/meko/{patient_path}/forget"
    )
    assert forgotten.status_code == 200
    forget.assert_called_once_with(PATIENT_PHONE, ASHA_ID)

    other_path = quote(OTHER_PHONE, safe="")
    not_owned = client.post(
        f"/dashboard/{ASHA_ID}/meko/{other_path}/forget"
    )
    assert not_owned.status_code == 404
    assert forget.call_count == 1


def test_rejects_invalid_correction_and_unauthorized_session(monkeypatch):
    monkeypatch.setattr(config, "MEKO_ENABLED", True)
    correct = Mock(return_value={"ok": True})
    monkeypatch.setattr(meko_client, "correct_patient_triage_memory", correct)
    client = _client(monkeypatch)
    patient_path = quote(PATIENT_PHONE, safe="")

    invalid = client.post(
        f"/dashboard/{ASHA_ID}/meko/{patient_path}/correct",
        json={"level": "URGENT", "status": "active"},
    )
    assert invalid.status_code == 400
    correct.assert_not_called()

    unauthenticated = app_module.app.test_client()
    response = unauthenticated.get(f"/dashboard/{ASHA_ID}/meko/data")
    assert response.status_code == 401


def test_asha_login_session_can_open_memory_page(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "unified_login",
        lambda _phone: {"role": "asha", "asha_id": ASHA_ID},
    )
    monkeypatch.setattr(config, "MEKO_ENABLED", False)
    client = app_module.app.test_client()

    login = client.post("/login", data={"phone": "5551230000"})
    assert login.status_code == 302
    with client.session_transaction() as session:
        assert session["asha_id"] == ASHA_ID

    page = client.get(f"/dashboard/{ASHA_ID}/meko")
    assert page.status_code == 200
    assert b"Meko is OFF" in page.data


def test_panel_escapes_patient_names_in_server_rendered_html(monkeypatch):
    monkeypatch.setattr(config, "MEKO_ENABLED", False)
    client = _client(monkeypatch)
    monkeypatch.setattr(
        app_module,
        "get_all_patients",
        lambda _asha_id: {
            PATIENT_PHONE: {
                "phone": PATIENT_PHONE,
                "name": "<img src=x onerror=alert(1)>",
                "week": 24,
            }
        },
    )

    response = client.get(f"/dashboard/{ASHA_ID}/meko")

    assert response.status_code == 200
    assert b"&lt;img" in response.data
    assert b"<img src=x onerror=alert(1)>" not in response.data


def test_meko_errors_fail_open_in_data_and_return_non_500_action(monkeypatch):
    monkeypatch.setattr(config, "MEKO_ENABLED", True)
    monkeypatch.setattr(
        meko_client,
        "get_patient_memory_snapshot",
        Mock(side_effect=RuntimeError("remote failure")),
    )
    monkeypatch.setattr(
        meko_client,
        "correct_patient_triage_memory",
        Mock(side_effect=RuntimeError("remote failure")),
    )
    client = _client(monkeypatch)

    data = client.get(f"/dashboard/{ASHA_ID}/meko/data")
    assert data.status_code == 200
    assert data.get_json()["patients"][0]["memory"]["state"] == "unavailable"

    patient_path = quote(PATIENT_PHONE, safe="")
    corrected = client.post(
        f"/dashboard/{ASHA_ID}/meko/{patient_path}/correct",
        json={"level": "RED", "status": "active"},
    )
    assert corrected.status_code == 503
    assert corrected.get_json()["error"] == "Meko unavailable"
