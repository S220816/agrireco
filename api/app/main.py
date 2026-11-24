import os
import time

import joblib
import pandas as pd
from fastapi import FastAPI
from pydantic import BaseModel, Field

API_VERSION = '1.0.0'

# Chargement du modele au demarrage, en mesurant le temps
t0 = time.time()
bundle = joblib.load(os.getenv('MODEL_PATH', 'model-1.0.0.joblib'))
LOAD_SECONDS = round(time.time() - t0, 3)

stats = {'calls': 0, 'total_ms': 0.0}

app = FastAPI(title='AgriReco API', version=API_VERSION)


class Soil(BaseModel):
    """Les 7 mesures d'un champ, avec des bornes realistes."""
    N: float = Field(ge=0, le=300, description='Azote')
    P: float = Field(ge=0, le=300, description='Phosphore')
    K: float = Field(ge=0, le=300, description='Potassium')
    temperature: float = Field(ge=-10, le=60, description='Temperature en degres C')
    humidity: float = Field(ge=0, le=100, description='Humidite relative en %')
    ph: float = Field(ge=0, le=14, description='pH du sol')
    rainfall: float = Field(ge=0, le=500, description='Pluviometrie en mm')


@app.get('/health')
def health():
    return {'status': 'ok', 'model_load_seconds': LOAD_SECONDS}


@app.get('/version')
def version():
    return {'api_version': API_VERSION, 'model_version': bundle['version']}


@app.post('/predict')
def predict(soil: Soil):
    start = time.perf_counter()
    x = pd.DataFrame([soil.model_dump()])[bundle['features']]
    proba = bundle['model'].predict_proba(x)[0]
    best = proba.argmax()
    ms = (time.perf_counter() - start) * 1000
    stats['calls'] += 1
    stats['total_ms'] += ms
    return {
        'crop': str(bundle['model'].classes_[best]),
        'confidence': round(float(proba[best]), 3),
        'model_version': bundle['version'],
        'latency_ms': round(ms, 2),
    }


@app.get('/metrics')
def metrics():
    n = stats['calls']
    return {'calls': n, 'avg_latency_ms': round(stats['total_ms'] / n, 2) if n else 0}
