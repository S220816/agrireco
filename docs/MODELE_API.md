# AgriReco — Partie A : modèle et API d'inférence

Responsable : Stephie (rôle A). Dossiers : `model/` et `api/`.

## 1. Jeu de données et tâche

| Élément | Valeur |
| --- | --- |
| Jeu de données | Crop Recommendation Dataset (Atharva Ingle), Kaggle |
| Licence | Apache 2.0 |
| Taille | 2 200 lignes, 8 colonnes, aucune valeur manquante, aucun doublon |
| Variables d'entrée | `N`, `P`, `K` (sol), `temperature` (°C), `humidity` (%), `ph`, `rainfall` (mm) |
| Variable à prédire | `label` : 22 cultures, 100 lignes chacune (classes équilibrées) |
| Tâche | Classification multiclasse : recommander une culture pour un champ |

## 2. Modèle

- Algorithme : forêt aléatoire (`RandomForestClassifier`, 200 arbres), scikit-learn 1.9.1.
- Reproductibilité : graine fixée (`SEED = 42`), `model/requirements.txt` figé, script `model/train.py`.
- Jeu de test séparé : 20 % des données (440 lignes), stratifié par culture.
- Modèle versionné : `model-1.0.0.joblib` (6,9 Mo), qui contient le modèle, la liste ordonnée des variables et la version.

### Métriques sur le jeu de test

| Modèle | Exactitude | F1 macro |
| --- | --- | --- |
| Référence naïve (toujours la culture la plus fréquente) | 0,0455 | 0,004 |
| Forêt aléatoire v1.0.0 | **0,9955** | **0,9955** |

2 erreurs sur 440 : riz prédit jute, haricot noir prédit maïs. Ce sont des cultures aux besoins proches (même N, P, K et humidité pour le riz et le jute, seule la pluie les distingue).

Ce score élevé n'est pas une fuite de données : le jeu est en partie synthétique (augmenté à partir de données indiennes) et les cultures sont très séparées.

Pour réentraîner :

```bash
cd model
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python train.py
```

## 3. API d'inférence (FastAPI, Python 3.12)

| Route | Rôle |
| --- | --- |
| `GET /health` | État de l'API et temps de chargement du modèle |
| `GET /version` | Version de l'API (fichier `api/VERSION`) et version du modèle |
| `POST /predict` | Recommande une culture pour un champ |
| `GET /metrics` | Nombre d'appels et latence moyenne |

Validation Pydantic : chaque mesure a des bornes réalistes (par exemple `ph` entre 0 et 14). Une mesure hors bornes ou un corps absent renvoient **422**.

Exemple d'appel :

```bash
curl -X POST https://<adresse-de-l-api>/predict \
  -H "Content-Type: application/json" \
  -d '{"N": 90, "P": 42, "K": 43, "temperature": 20.9, "humidity": 82, "ph": 6.5, "rainfall": 202.9}'
```

Réponse :

```json
{"crop": "rice", "confidence": 0.935, "model_version": "1.0.0", "latency_ms": 34.23}
```

Ce format est le contrat utilisé par le worker de la partie B.

### Tests

`api/tests/test_api.py`, lancés avec `python -m pytest -v` : 4 tests au vert.

| Test | Vérifie |
| --- | --- |
| `test_predict_succes` | Un champ de riz renvoie 200, `rice`, une confiance entre 0 et 1, la version 1.0.0 |
| `test_predict_entree_invalide` | Un pH de 20 renvoie 422 |
| `test_predict_corps_absent` | Une requête sans corps renvoie 422 |
| `test_health_et_version` | `/health` et `/version` répondent correctement |

## 4. Image Docker

- Dockerfile **multi-étapes** basé sur `python:3.12-slim` : la première étape installe les bibliothèques et supprime leurs dossiers de tests et fichiers temporaires, la seconde ne copie que le résultat, le code, le modèle et `VERSION`.
- **Utilisateur non root** (`appuser`).
- **HEALTHCHECK** sur `/health`.
- Taille de l'image décompressée : **413 Mo** (limite du sujet : 500 Mo), mesurée avec `docker run --rm agrireco-api:1.0.0 du -sh /`.
- L'image destinée à Azure est construite en `linux/amd64` par GitHub Actions et publiée sur GitHub Container Registry.

Test en local :

```bash
cd api
docker build -t agrireco-api:1.0.0 .
docker run -d --name agrireco -p 8000:8000 agrireco-api:1.0.0
curl http://localhost:8000/health
```

## 5. Télémétrie

La distribution Azure Monitor OpenTelemetry (`azure-monitor-opentelemetry`) est activée seulement si la variable `APPLICATIONINSIGHTS_CONNECTION_STRING` est définie (donc sur Azure, pas en local).

Deux métriques personnalisées :

| Métrique | Type | Contenu |
| --- | --- | --- |
| `inferences` | Compteur | Nombre de prédictions, par culture et version du modèle |
| `model_latency_ms` | Histogramme | Temps de calcul du modèle, en millisecondes |

## 6. Déploiement sur Azure Container Apps

| Paramètre | Valeur |
| --- | --- |
| Application | `api-agrinb27`, environnement `cae-agrinb27`, région `francecentral` |
| Ressources | 0,25 vCPU, 0,5 Gio |
| Réplicas | minimum 0, maximum 2 (mise à l'échelle jusqu'à zéro) |
| Sondes | liveness et readiness sur `/health` |
| Configuration | uniquement par variables d'environnement |

À compléter après le déploiement :
- [ ] Capture : `curl https://<adresse>/predict` qui répond depuis Internet
- [ ] Capture : nombre de réplicas à 0 sans trafic
- [ ] Capture : métriques `inferences` et `model_latency_ms` dans Application Insights

## 7. Preuves (dossier `docs/captures/`)

- [ ] Résultat de `train.py` (métriques)
- [ ] Page `/docs` de l'API et réponse de `/predict`
- [ ] Sortie de `pytest` (4 passed)
- [ ] `docker ps` avec l'état « healthy » et la taille de l'image
