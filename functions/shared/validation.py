"""Vérification d'un fichier CSV d'analyses de sol (utilisée par le dispatcher).

Le dispatcher ne juge que la FORME du fichier (extension, taille, encodage,
colonnes, nombres). Les VALEURS hors limites (ex. pH = 20) sont jugées par
l'API, qui répond 422 : le worker enregistre alors la ligne comme invalide.
"""
import csv
import io
import os

# Colonnes attendues, dans n'importe quel ordre (format convenu avec A et C).
ID_COLUMN = "field_id"
MEASURE_COLUMNS = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
REQUIRED_COLUMNS = [ID_COLUMN] + MEASURE_COLUMNS

MAX_BYTES = int(os.getenv("MAX_FILE_BYTES", str(1024 * 1024)))  # 1 Mo
MAX_ROWS = int(os.getenv("MAX_ROWS", "1000"))


class InvalidFile(Exception):
    """Le fichier ne respecte pas le format : il part dans rejected/."""


def parse_rows(name: str, data: bytes) -> list[dict]:
    """Vérifie le fichier et renvoie ses lignes ; lève InvalidFile sinon."""
    if not name.lower().endswith(".csv"):
        raise InvalidFile("extension attendue : .csv")
    if len(data) == 0:
        raise InvalidFile("fichier vide")
    if len(data) > MAX_BYTES:
        raise InvalidFile(f"fichier trop gros ({len(data)} octets, max {MAX_BYTES})")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidFile("encodage attendu : UTF-8")

    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise InvalidFile(f"colonnes manquantes : {', '.join(missing)}")

    rows = []
    for line_no, raw in enumerate(reader, start=2):  # ligne 1 = en-tête
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        if not row.get(ID_COLUMN):
            raise InvalidFile(f"ligne {line_no} : {ID_COLUMN} vide")
        clean = {ID_COLUMN: row[ID_COLUMN]}
        for col in MEASURE_COLUMNS:
            try:
                clean[col] = float(row[col])
            except (TypeError, ValueError):
                raise InvalidFile(f"ligne {line_no} : {col} n'est pas un nombre ({row.get(col)!r})")
        rows.append(clean)
        if len(rows) > MAX_ROWS:
            raise InvalidFile(f"trop de lignes (max {MAX_ROWS})")
    if not rows:
        raise InvalidFile("aucune ligne de données")
    return rows
