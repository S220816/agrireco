"""AgriReco — partie B : le pipeline (une seule Function App, 3 fonctions).

dispatcher : un CSV arrive dans input/ (Event Grid) -> vérifie -> file d'attente ou rejected/
worker     : un message de la file -> appelle l'API pour chaque ligne -> Cosmos DB + output/
recent     : GET /api/recent -> derniers résultats, pour le tableau de bord
"""
import json
import logging
import os

import azure.functions as func

from shared import processing, store
from shared.validation import InvalidFile, parse_rows

app = func.FunctionApp()

QUEUE_NAME = os.getenv("QUEUE_NAME", "predictions")
CONNECTION = "AGRI_STORAGE"  # préfixe des réglages de connexion au stockage

# En Azure (plan Flex Consumption), le déclencheur Blob passe par Event Grid.
# En local, sans Event Grid, mettre BLOB_TRIGGER_SOURCE=LogsAndContainerScan.
_SOURCE = (
    func.BlobSource.LOGS_AND_CONTAINER_SCAN
    if os.getenv("BLOB_TRIGGER_SOURCE") == "LogsAndContainerScan"
    else func.BlobSource.EVENT_GRID
)


@app.function_name("dispatcher")
@app.blob_trigger(arg_name="blob", path="input/{name}", connection=CONNECTION, source=_SOURCE)
@app.queue_output(arg_name="msg", queue_name=QUEUE_NAME, connection=CONNECTION)
def dispatcher(blob: func.InputStream, msg: func.Out[str]) -> None:
    name = blob.name.split("/", 1)[1] if "/" in blob.name else blob.name
    data = blob.read()
    try:
        rows = parse_rows(name, data)
    except InvalidFile as exc:
        logging.warning("REJET %s : %s", name, exc)
        store.move_to_rejected(name, data, str(exc))
        return
    msg.set(json.dumps({"file": name, "rows": len(rows), "received_at": store.now_iso()}))
    logging.info("ACCEPTE %s (%d lignes) -> file %s", name, len(rows), QUEUE_NAME)


@app.function_name("worker")
@app.queue_trigger(arg_name="msg", queue_name=QUEUE_NAME, connection=CONNECTION)
def worker(msg: func.QueueMessage) -> None:
    job = json.loads(msg.get_body().decode("utf-8"))
    name = job["file"]
    logging.info("TRAITEMENT %s (essai %s)", name, msg.dequeue_count)
    data = store.read_blob(store.INPUT_CONTAINER, name)
    docs, csv_bytes = processing.process_file(name, data, store.results_store())
    store.write_blob(store.OUTPUT_CONTAINER, processing.output_name(name), csv_bytes)
    ok = sum(1 for d in docs if d["status"] == "ok")
    logging.info("TERMINE %s : %d lignes, %d prédites, %d invalides", name, len(docs), ok, len(docs) - ok)


@app.function_name("recent")
@app.route(route="recent", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def recent(req: func.HttpRequest) -> func.HttpResponse:
    try:
        limit = max(1, min(int(req.params.get("limit", "50")), 200))
    except ValueError:
        limit = 50
    items = store.results_store().recent(limit)
    return func.HttpResponse(
        json.dumps({"count": len(items), "items": items}, ensure_ascii=False),
        mimetype="application/json",
        headers={"Cache-Control": "no-store"},
    )
