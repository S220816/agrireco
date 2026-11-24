"""Logique du worker, séparée d'Azure Functions pour pouvoir la tester."""
import csv
import io
import logging
import os

from . import api_client
from .store import now_iso
from .validation import ID_COLUMN, MEASURE_COLUMNS, parse_rows

RESULT_COLUMNS = [ID_COLUMN] + MEASURE_COLUMNS + [
    "crop", "confidence", "model_version", "latency_ms", "status", "error",
]


def doc_id(file_name: str, row_index: int) -> str:
    """Identifiant fixe : si le message est rejoué, on remplace au lieu de dupliquer."""
    safe = file_name.replace("/", "_").replace("\\", "_").replace("?", "_").replace("#", "_")
    return f"{safe}-{row_index}"


def process_file(file_name: str, data: bytes, store, predict=api_client.predict) -> tuple[list[dict], bytes]:
    """Prédit chaque ligne, enregistre les résultats, renvoie (documents, CSV de sortie).

    Une erreur autre que 422 remonte : le runtime réessaie le message
    (5 fois au total) puis l'envoie en file poison.
    """
    rows = parse_rows(file_name, data)
    docs = []
    for i, row in enumerate(rows, start=1):
        doc = {
            "id": doc_id(file_name, i),
            "fileName": file_name,
            "row": i,
            ID_COLUMN: row[ID_COLUMN],
            "inputs": {c: row[c] for c in MEASURE_COLUMNS},
            "processed_at": now_iso(),
        }
        try:
            pred = predict(row)
            doc.update(
                status="ok",
                crop=pred["crop"],
                confidence=pred["confidence"],
                model_version=pred["model_version"],
                latency_ms=pred.get("latency_ms"),
            )
        except api_client.InvalidMeasure as exc:
            logging.warning("ligne %s de %s invalide (422) : %s", i, file_name, exc)
            doc.update(status="invalid", crop=None, confidence=None, model_version=None, error=str(exc))
        store.upsert(doc)
        docs.append(doc)

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for d in docs:
        writer.writerow({**d["inputs"], **d, "error": d.get("error", "")})
    return docs, out.getvalue().encode("utf-8")


def output_name(file_name: str) -> str:
    base = os.path.basename(file_name)
    stem = base[:-4] if base.lower().endswith(".csv") else base
    folder = os.path.dirname(file_name)
    return f"{folder + '/' if folder else ''}{stem}_resultats.csv"
