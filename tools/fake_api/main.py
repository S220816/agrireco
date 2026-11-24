"""Fausse API AgriReco : même contrat que l'API de A, sans modèle.

Sert à faire tourner le pipeline de bout en bout avant que la vraie API soit prête.
Lancer :  uvicorn main:app --port 8000
Variables : FAKE_FAIL_RATE=1.0 pour simuler une panne (500) et tester la file poison.
"""
import os
import random
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="AgriReco - fausse API", version="0.0.0-fake")

MODEL_VERSION = "0.0.0-fake"


class SoilSample(BaseModel):
    # Bornes physiques : en dehors, FastAPI répond 422 automatiquement.
    N: float = Field(ge=0, le=300)
    P: float = Field(ge=0, le=300)
    K: float = Field(ge=0, le=300)
    temperature: float = Field(ge=-10, le=60)
    humidity: float = Field(ge=0, le=100)
    ph: float = Field(ge=0, le=14)
    rainfall: float = Field(ge=0, le=5000)


def fake_crop(s: SoilSample) -> tuple[str, float]:
    """Règles grossières, juste pour avoir des réponses variées et stables."""
    if s.rainfall > 180 and s.humidity > 75:
        return "rice", 0.97
    if s.rainfall < 60 and s.temperature > 25:
        return "chickpea", 0.88
    if s.K > 150:
        return "grapes", 0.91
    if s.ph < 5.5:
        return "coffee", 0.84
    if s.humidity > 85:
        return "banana", 0.86
    return "maize", 0.79


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"model_version": MODEL_VERSION}


@app.post("/predict")
def predict(sample: SoilSample):
    start = time.perf_counter()
    if random.random() < float(os.getenv("FAKE_FAIL_RATE", "0")):
        raise HTTPException(status_code=500, detail="panne simulée")
    crop, conf = fake_crop(sample)
    return {
        "crop": crop,
        "confidence": conf,
        "model_version": MODEL_VERSION,
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
    }
