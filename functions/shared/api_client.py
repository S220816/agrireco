"""Appel de l'API de prédiction (partie A, ou la fausse API en attendant).

Contrat convenu avec A :
  POST {API_URL}/predict
  {"N": 90, "P": 42, "K": 43, "temperature": 20.9, "humidity": 82, "ph": 6.5, "rainfall": 202.9}
  -> 200 {"crop": "rice", "confidence": 0.97, "model_version": "1.0.0", "latency_ms": 3.2}
  -> 422 si une mesure est invalide
"""
import os

import requests

from .validation import MEASURE_COLUMNS

# L'API sur Container Apps descend à 0 sans trafic : le premier appel peut être lent.
TIMEOUT_S = float(os.getenv("API_TIMEOUT_SECONDS", "30"))


_session = None


def get_session() -> requests.Session:
    """Un seul client HTTP, créé une fois et réutilisé pour toutes les lignes (connexions gardées ouvertes)."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


class InvalidMeasure(Exception):
    """L'API a répondu 422 : la ligne est invalide, inutile de réessayer."""


def predict(row: dict, session=None) -> dict:
    """Renvoie la prédiction pour une ligne.

    - 200 -> dict de l'API
    - 422 -> InvalidMeasure (pas de nouvel essai)
    - autre code, délai dépassé, API injoignable -> exception (le message sera
      réessayé, puis envoyé en file poison après 5 échecs)
    """
    base = os.environ["API_URL"].rstrip("/")
    payload = {c: row[c] for c in MEASURE_COLUMNS}
    http = session or get_session()
    resp = http.post(f"{base}/predict", json=payload, timeout=TIMEOUT_S)
    if resp.status_code == 422:
        raise InvalidMeasure(resp.text[:300])
    resp.raise_for_status()
    body = resp.json()
    for key in ("crop", "confidence", "model_version"):
        if key not in body:
            raise ValueError(f"réponse de l'API sans '{key}' : {body}")
    return body
