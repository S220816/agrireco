"""AgriReco — partie B : le pipeline (une seule Function App, 3 fonctions).

dispatcher : Event Grid signale un CSV dans input/ -> vérifie -> file inference-requests ou rejected/
worker     : un message de la file -> appelle l'API pour chaque ligne -> Cosmos DB + output/
recent     : GET /api/recent -> 20 dernières inférences, pour le tableau de bord
"""
import json
import logging
import os

import azure.functions as func

from shared import processing, store
from shared.validation import MAX_BYTES, InvalidFile, parse_rows

app = func.FunctionApp()

QUEUE_NAME = os.getenv("QUEUE_NAME", "inference-requests")  # poison : inference-requests-poison
CONNECTION = "AGRI_STORAGE"  # préfixe des réglages de connexion au stockage
RECENT_DEFAULT = 20
INPUT_PREFIX = f"/blobServices/default/containers/{store.INPUT_CONTAINER}/blobs/"


def blob_event(event: func.EventGridEvent) -> dict | None:
    """Extrait nom, ETag et taille d'un événement BlobCreated sur input/, sinon None.

    L'abonnement Event Grid est déjà filtré (BlobCreated + input/) ;
    on revérifie ici pour ne jamais traiter un autre conteneur.
    """
    if event.event_type != "Microsoft.Storage.BlobCreated" or not event.subject.startswith(INPUT_PREFIX):
        return None
    data = event.get_json() or {}
    return {
        "blob": event.subject[len(INPUT_PREFIX):],
        "etag": data.get("eTag", ""),
        "size": data.get("contentLength"),
        "event_time": event.event_time.isoformat() if event.event_time else None,
    }


@app.function_name("dispatcher")
@app.event_grid_trigger(arg_name="event")
@app.queue_output(arg_name="msg", queue_name=QUEUE_NAME, connection=CONNECTION)
def dispatcher(event: func.EventGridEvent, msg: func.Out[str]) -> None:
    info = blob_event(event)
    if info is None:
        logging.info("événement ignoré : %s %s", event.event_type, event.subject)
        return
    name = info["blob"]
    if info["size"] is not None and info["size"] > MAX_BYTES:
        reason = f"fichier trop gros ({info['size']} octets > {MAX_BYTES})"
        logging.warning("REJET %s : %s", name, reason)
        store.move_to_rejected(name, store.read_blob(store.INPUT_CONTAINER, name), reason)
        return
    data = store.read_blob(store.INPUT_CONTAINER, name)
    try:
        rows = parse_rows(name, data)
    except InvalidFile as exc:
        logging.warning("REJET %s : %s", name, exc)
        store.move_to_rejected(name, data, str(exc))
        return
    # Message de la file : nom du blob, ETag (version), taille, heure.
    msg.set(json.dumps({
        "blob": name,
        "etag": info["etag"],
        "size": info["size"] if info["size"] is not None else len(data),
        "time": store.now_iso(),
        "rows": len(rows),
    }))
    logging.info("ACCEPTE %s (etag %s, %d lignes) -> file %s", name, info["etag"], len(rows), QUEUE_NAME)


@app.function_name("worker")
@app.queue_trigger(arg_name="msg", queue_name=QUEUE_NAME, connection=CONNECTION)
def worker(msg: func.QueueMessage) -> None:
    job = json.loads(msg.get_body().decode("utf-8"))
    name, etag = job["blob"], job.get("etag", "")
    logging.info("TRAITEMENT %s etag %s (essai %s)", name, etag, msg.dequeue_count)
    data = store.read_blob(store.INPUT_CONTAINER, name, etag=etag)
    if data is None:  # le fichier a été remplacé : la nouvelle version a son propre message
        logging.info("IGNORE %s : version %s remplacée", name, etag)
        return
    docs, csv_bytes = processing.process_file(name, data, store.results_store(), etag=etag)
    for d in docs:  # un JSON complet par inférence
        store.write_blob(store.OUTPUT_CONTAINER, processing.json_name(d), processing.to_json(d), "application/json")
    store.write_blob(store.OUTPUT_CONTAINER, processing.output_name(name), csv_bytes)
    ok = sum(1 for d in docs if d["status"] == "ok")
    logging.info("TERMINE %s : %d lignes, %d prédites, %d invalides", name, len(docs), ok, len(docs) - ok)


@app.function_name("recent")
@app.route(route="recent", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def recent(req: func.HttpRequest) -> func.HttpResponse:
    try:
        limit = max(1, min(int(req.params.get("limit", RECENT_DEFAULT)), 100))
    except ValueError:
        limit = RECENT_DEFAULT
    items = store.results_store().recent(limit)
    return func.HttpResponse(
        json.dumps({"count": len(items), "items": items}, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )
