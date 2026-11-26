from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

RIZ = {'N': 90, 'P': 42, 'K': 43, 'temperature': 20.9,
       'humidity': 82, 'ph': 6.5, 'rainfall': 202.9}


def test_predict_succes():
    r = client.post('/predict', json=RIZ)
    assert r.status_code == 200
    body = r.json()
    assert body['crop'] == 'rice'
    assert 0 <= body['confidence'] <= 1
    assert body['model_version'] == '1.0.0'


def test_predict_entree_invalide():
    r = client.post('/predict', json={**RIZ, 'ph': 20})
    assert r.status_code == 422


def test_predict_corps_absent():
    r = client.post('/predict')
    assert r.status_code == 422


def test_health_et_version():
    assert client.get('/health').json()['status'] == 'ok'
    assert client.get('/version').json()['model_version'] == '1.0.0'
