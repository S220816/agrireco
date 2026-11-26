"""Test de bout en bout en local, SANS Azure : Azurite (émulateur de stockage) + fausse API.

Il rejoue ce que fait le runtime Azure Functions avec NOS vraies fonctions :
  1. dépose les CSV de samples/ dans input/
  2. appelle le dispatcher avec un événement BlobCreated (comme Event Grid)
  3. lit la file et appelle le worker ; après 5 échecs -> file poison
  4. appelle recent (comme le tableau de bord)

Prérequis : azurite lancé, fausse API sur API_URL (voir docs/PIPELINE.md).
Usage : python tools/local_e2e.py [--fail]   (--fail : l'API est coupée -> test de la file poison)
"""
import base64
import datetime
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "functions"))
os.environ.setdefault("AGRI_STORAGE", "UseDevelopmentStorage=true")
os.environ.setdefault("API_URL", "http://127.0.0.1:8000")
os.environ.setdefault("LOCAL_STORE_PATH", str(ROOT / "functions" / "local_results.json"))

import azure.functions as func  # noqa: E402
from azure.storage.queue import QueueClient  # noqa: E402

import function_app  # noqa: E402
from shared import store  # noqa: E402

MAX_DEQUEUE = 5  # même valeur que host.json
QUEUE = os.getenv("QUEUE_NAME", "inference-requests")
CONN = os.environ["AGRI_STORAGE"]


_FUNCS = {f.get_function_name(): f.get_user_function() for f in function_app.app.get_functions()}


def user_fn(name):
    return _FUNCS[name]


class Out:
    def __init__(self):
        self.value = None

    def set(self, v):
        self.value = v


class Msg(func.QueueMessage):
    def __init__(self, body, dequeue_count):
        super().__init__(body=body)
        self._dc = dequeue_count

    @property
    def dequeue_count(self):
        return self._dc


def setup():
    bs = store.blob_service()
    for c in (store.INPUT_CONTAINER, store.REJECTED_CONTAINER, store.OUTPUT_CONTAINER):
        try:
            bs.create_container(c)
        except Exception:
            pass
    for q in (QUEUE, f"{QUEUE}-poison"):
        try:
            QueueClient.from_connection_string(CONN, q).create_queue()
        except Exception:
            pass


def main():
    setup()
    queue = QueueClient.from_connection_string(CONN, QUEUE)
    poison = QueueClient.from_connection_string(CONN, f"{QUEUE}-poison")
    queue.clear_messages()
    poison.clear_messages()
    dispatcher, worker, recent = user_fn("dispatcher"), user_fn("worker"), user_fn("recent")

    print("== 1. dépôt + dispatcher")
    for f in sorted((ROOT / "samples").glob("*.csv")):
        data = f.read_bytes()
        store.write_blob(store.INPUT_CONTAINER, f.name, data)
        props = store.blob_service().get_blob_client(store.INPUT_CONTAINER, f.name).get_blob_properties()
        event = func.EventGridEvent(
            id=f.name, topic="local", subject=f"/blobServices/default/containers/{store.INPUT_CONTAINER}/blobs/{f.name}",
            event_type="Microsoft.Storage.BlobCreated", data_version="1",
            event_time=datetime.datetime.now(datetime.timezone.utc),
            data={"eTag": props.etag, "contentLength": props.size, "url": f"local/{f.name}"},
        )
        out = Out()
        dispatcher(event, out)
        if out.value:
            queue.send_message(base64.b64encode(out.value.encode()).decode())  # Functions encode en base64
            print(f"   {f.name:24s} -> file d'attente")
        else:
            print(f"   {f.name:24s} -> rejected/")

    print("== 2. worker")
    deadline = time.time() + 120
    while time.time() < deadline:
        msgs = list(queue.receive_messages(messages_per_page=8, visibility_timeout=1))
        if not msgs:
            if queue.get_queue_properties().approximate_message_count == 0:
                break
            time.sleep(1)
            continue
        for m in msgs:
            body = base64.b64decode(m.content)
            try:
                worker(Msg(body, m.dequeue_count))
                queue.delete_message(m)
                print(f"   OK      {json.loads(body)['blob']} (essai {m.dequeue_count})")
            except Exception as exc:
                print(f"   ECHEC   {json.loads(body)['blob']} (essai {m.dequeue_count}) : {type(exc).__name__}")
                if m.dequeue_count >= MAX_DEQUEUE:
                    poison.send_message(m.content)
                    queue.delete_message(m)
                    print(f"   POISON  {json.loads(body)['blob']} -> {QUEUE}-poison")

    print("== 3. état du stockage")
    bs = store.blob_service()
    for c in (store.INPUT_CONTAINER, store.REJECTED_CONTAINER, store.OUTPUT_CONTAINER):
        print(f"   {c}/ :", [b.name for b in bs.get_container_client(c).list_blobs()])
    print(f"   {QUEUE}-poison :", poison.get_queue_properties().approximate_message_count, "message(s)")

    print("== 4. recent (tableau de bord)")
    resp = recent(func.HttpRequest("GET", "/api/recent", params={"limit": "5"}, body=b""))
    body = json.loads(resp.get_body())
    for d in body["items"]:
        print(f"   {d['field_id']:18s} {str(d.get('crop')):10s} {d.get('confidence')} {d['status']}")


if __name__ == "__main__":
    if "--fail" in sys.argv:
        os.environ["API_URL"] = "http://127.0.0.1:9"  # personne n'écoute ici
    main()
