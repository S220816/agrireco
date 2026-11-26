import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLES = ROOT.parent / "samples"

from shared import api_client, processing  # noqa: E402
from shared.store import LocalStore  # noqa: E402
from shared.validation import InvalidFile, parse_rows  # noqa: E402

GOOD = b"field_id,N,P,K,temperature,humidity,ph,rainfall\nA,90,42,43,20.9,82,6.5,202.9\n"


# ---------------- dispatcher : vérification du fichier
def test_valid_file_is_parsed():
    rows = parse_rows("ferme.csv", GOOD)
    assert rows == [{"field_id": "A", "N": 90.0, "P": 42.0, "K": 43.0, "temperature": 20.9,
                     "humidity": 82.0, "ph": 6.5, "rainfall": 202.9}]


def test_columns_in_any_order_and_bom():
    data = "﻿ph,field_id,N,P,K,temperature,humidity,rainfall\n6.5,A,1,2,3,4,5,6\n".encode()
    assert parse_rows("x.csv", data)[0]["ph"] == 6.5


@pytest.mark.parametrize("name,data,reason", [
    ("ferme.txt", GOOD, "extension"),
    ("vide.csv", b"", "vide"),
    ("x.csv", b"field_id,N\nA,1\n", "colonnes manquantes"),
    ("x.csv", b"field_id,N,P,K,temperature,humidity,ph,rainfall\nA,abc,42,43,20,82,6.5,200\n", "pas un nombre"),
    ("x.csv", b"field_id,N,P,K,temperature,humidity,ph,rainfall\n", "aucune ligne"),
    ("x.csv", b"\xff\xfe\x00bad", "UTF-8"),
])
def test_invalid_files_are_rejected(name, data, reason):
    with pytest.raises(InvalidFile, match=reason):
        parse_rows(name, data)


def test_demo_samples():
    for f in ["ferme_1_riziere.csv", "ferme_2_plaine.csv", "ferme_3_verger.csv"]:
        assert parse_rows(f, (SAMPLES / f).read_bytes())
    with pytest.raises(InvalidFile):
        parse_rows("fichier_casse.csv", (SAMPLES / "fichier_casse.csv").read_bytes())


# ---------------- worker : traitement
def fake_predict(row):
    if row["ph"] > 14:
        raise api_client.InvalidMeasure('{"detail":"ph"}')
    return {"crop": "rice", "confidence": 0.97, "model_version": "1.0.0", "latency_ms": 3.2}


def test_process_file_writes_results_and_handles_422(tmp_path):
    store = LocalStore(str(tmp_path / "r.json"))
    data = (SAMPLES / "ferme_3_verger.csv").read_bytes()  # contient un pH de 20
    docs, out = processing.process_file("ferme_3_verger.csv", data, store, predict=fake_predict)
    assert [d["status"] for d in docs] == ["ok", "ok", "invalid"]
    assert docs[0]["id"] == "ferme_3_verger.csv-1"
    assert len(store.recent(10)) == 3
    assert out.decode().splitlines()[0].startswith("field_id,N,P,K")


def test_replay_does_not_duplicate(tmp_path):
    store = LocalStore(str(tmp_path / "r.json"))
    for _ in range(2):  # le message est rejoué : même id -> remplacé
        processing.process_file("f.csv", GOOD, store, predict=fake_predict)
    assert len(store.recent(10)) == 1


def test_api_failure_propagates_for_retry(tmp_path):
    def broken(row):
        raise ConnectionError("API injoignable")
    with pytest.raises(ConnectionError):
        processing.process_file("f.csv", GOOD, LocalStore(str(tmp_path / "r.json")), predict=broken)


def test_output_name():
    assert processing.output_name("ferme.csv") == "ferme_resultats.csv"
    assert processing.output_name("coop/ferme.csv") == "coop/ferme_resultats.csv"


# ---------------- api_client : contrat avec l'API de A
class FakeResp:
    def __init__(self, code, body):
        self.status_code, self._body, self.text = code, body, json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return self.resp


ROW = parse_rows("f.csv", GOOD)[0]


def test_api_contract(monkeypatch):
    monkeypatch.setenv("API_URL", "http://api/")
    s = FakeSession(FakeResp(200, {"crop": "rice", "confidence": 0.97, "model_version": "1.0.0", "latency_ms": 3.2}))
    assert api_client.predict(ROW, session=s)["crop"] == "rice"
    url, payload, _ = s.calls[0]
    assert url == "http://api/predict"
    assert payload == {"N": 90.0, "P": 42.0, "K": 43.0, "temperature": 20.9, "humidity": 82.0, "ph": 6.5, "rainfall": 202.9}


def test_api_422_is_not_retried(monkeypatch):
    monkeypatch.setenv("API_URL", "http://api")
    with pytest.raises(api_client.InvalidMeasure):
        api_client.predict(ROW, session=FakeSession(FakeResp(422, {"detail": "ph"})))


def test_api_500_raises(monkeypatch):
    monkeypatch.setenv("API_URL", "http://api")
    with pytest.raises(RuntimeError):
        api_client.predict(ROW, session=FakeSession(FakeResp(500, {"detail": "boom"})))


def test_function_app_indexes_three_functions():
    import function_app
    assert sorted(f.get_function_name() for f in function_app.app.get_functions()) == ["dispatcher", "recent", "worker"]


# ---------------- identifiant : nom du blob + ETag + ligne
def test_id_uses_blob_name_etag_and_row(tmp_path):
    store = LocalStore(str(tmp_path / "r.json"))
    docs, _ = processing.process_file("coop/f.csv", GOOD, store, predict=fake_predict, etag='"0x8DCABC"')
    assert docs[0]["id"] == "coop_f.csv-0x8DCABC-1"
    assert docs[0]["etag"] == '"0x8DCABC"'


def test_new_version_of_same_file_is_a_new_inference(tmp_path):
    store = LocalStore(str(tmp_path / "r.json"))
    processing.process_file("f.csv", GOOD, store, predict=fake_predict, etag='"v1"')
    processing.process_file("f.csv", GOOD, store, predict=fake_predict, etag='"v1"')  # rejeu
    processing.process_file("f.csv", GOOD, store, predict=fake_predict, etag='"v2"')  # nouveau dépôt
    assert len(store.recent(10)) == 2


def test_json_output_per_inference(tmp_path):
    docs, _ = processing.process_file("ferme.csv", GOOD, LocalStore(str(tmp_path / "r.json")),
                                      predict=fake_predict, etag='"e1"')
    assert processing.json_name(docs[0]) == "ferme/ferme.csv-e1-1.json"
    body = json.loads(processing.to_json(docs[0]))
    assert body["crop"] == "rice" and body["inputs"]["ph"] == 6.5 and body["etag"] == '"e1"'


# ---------------- api_client : un seul requests.Session réutilisé
def test_session_is_created_once():
    assert api_client.get_session() is api_client.get_session()


# ---------------- dispatcher : lecture de l'événement Event Grid
def _event(subject, event_type="Microsoft.Storage.BlobCreated"):
    import datetime

    import azure.functions as func
    return func.EventGridEvent(
        id="1", data={"eTag": '"0x1"', "contentLength": 120, "url": "https://x/input/f.csv"},
        topic="t", subject=subject, event_type=event_type,
        event_time=datetime.datetime(2026, 11, 26, tzinfo=datetime.timezone.utc), data_version="1",
    )


def test_event_grid_blob_created_in_input():
    import function_app
    info = function_app.blob_event(_event("/blobServices/default/containers/input/blobs/coop/f.csv"))
    assert info["blob"] == "coop/f.csv" and info["etag"] == '"0x1"' and info["size"] == 120


@pytest.mark.parametrize("subject,etype", [
    ("/blobServices/default/containers/output/blobs/f.csv", "Microsoft.Storage.BlobCreated"),
    ("/blobServices/default/containers/input/blobs/f.csv", "Microsoft.Storage.BlobDeleted"),
])
def test_other_events_are_ignored(subject, etype):
    import function_app
    assert function_app.blob_event(_event(subject, etype)) is None


def test_recent_returns_20_by_default(tmp_path, monkeypatch):
    import azure.functions as func

    import function_app
    from shared import store as store_mod
    s = LocalStore(str(tmp_path / "r.json"))
    for i in range(25):
        s.upsert({"id": str(i), "processed_at": f"2026-11-26T10:{i:02d}:00+00:00"})
    monkeypatch.setattr(store_mod, "_store", s)
    body = json.loads(function_app.recent(func.HttpRequest("GET", "/api/recent", body=b"")).get_body())
    assert body["count"] == 20 and body["items"][0]["id"] == "24"
