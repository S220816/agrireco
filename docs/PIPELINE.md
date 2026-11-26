# AgriReco — Partie B : le pipeline

Responsable : Sarah Djemouai (rôle B). Ce document décrit le pipeline, comment le tester en local et ce qu'il faut créer dans Azure.

## Parcours d'un fichier

```
CSV déposé dans input/
   │  Event Grid : événement BlobCreated (filtré sur input/)
   ▼
dispatcher ── fichier invalide ──► rejected/<fichier> + <fichier>.reason.txt
   │ fichier valide : message {blob, etag, size, time}
   ▼
Storage Queue "inference-requests" ── 5 échecs ──► "inference-requests-poison"
   │
   ▼
worker ── POST /predict (API de A) pour chaque ligne, un seul requests.Session réutilisé
   │        200 -> culture + confiance        422 -> ligne "invalide" (pas de nouvel essai)
   ├──► Cosmos DB (un document par inférence, upsert, id = <blob>-<etag>-<ligne>)
   ├──► output/<fichier>/<id>.json (un JSON complet par inférence)
   └──► output/<fichier>_resultats.csv (récapitulatif)

Tableau de bord ($web) ── toutes les 30 s ──► GET /api/recent ──► Cosmos DB
```

| Fonction | Déclencheur | Rôle |
| --- | --- | --- |
| `dispatcher` | **Event Grid** (`BlobCreated` sur `input/`) | Lit nom, ETag, taille dans l'événement ; vérifie extension `.csv`, taille ≤ 1 Mo, UTF-8, colonnes, nombres ; valide → message dans la file, invalide → `rejected/` |
| `worker` | Queue `inference-requests` | Appelle l'API ligne par ligne, écrit Cosmos DB et `output/` (JSON par inférence + CSV) |
| `recent` | HTTP `GET /api/recent` (anonyme, 20 par défaut, `?limit=` jusqu'à 100) | Renvoie les 20 dernières inférences au tableau de bord |

**Message de la file** :

```json
{"blob": "ferme_1.csv", "etag": "\"0x8DD0F3A1B2C4E5F\"", "size": 214, "time": "2026-11-26T09:17:40+00:00", "rows": 3}
```

## Choix du déclencheur : Event Grid

Le dispatcher utilise le **déclencheur Event Grid** (demandé par le sujet) plutôt que le déclencheur Blob :

- l'événement `BlobCreated` contient directement ce que le message de la file doit porter : nom du blob (dans `subject`), **ETag** (`data.eTag`) et **taille** (`data.contentLength`) ; le déclencheur Blob donne le contenu mais pas facilement l'ETag ;
- l'abonnement Event Grid se crée vers la fonction par son identifiant Azure (`--endpoint-type azurefunction`), sans URL de webhook ni clé à manipuler ;
- l'abonnement est **filtré** : type `Microsoft.Storage.BlobCreated` uniquement, et sujet commençant par `/blobServices/default/containers/input/`. Le code revérifie ces deux conditions, donc un fichier écrit dans `output/` ou `rejected/` ne relance jamais le pipeline.

Le worker relit le fichier **à cet ETag précis** : si le fichier a été remplacé entre-temps, l'ancien message est ignoré (le nouveau dépôt a son propre événement)

## Contrats

**CSV d'entrée** (colonnes dans n'importe quel ordre, séparateur virgule, UTF-8) :

```
field_id,N,P,K,temperature,humidity,ph,rainfall
F1-parcelle-nord,90,42,43,20.9,82,6.5,202.9
```

**API (partie A)** — `POST {API_URL}/predict`

```json
{"N": 90, "P": 42, "K": 43, "temperature": 20.9, "humidity": 82, "ph": 6.5, "rainfall": 202.9}
```
→ `200 {"crop": "rice", "confidence": 0.97, "model_version": "1.0.0", "latency_ms": 3.2}`
→ `422` si une mesure est invalide (ex. pH = 20).

**Document Cosmos DB** (base `agrireco`, conteneur `predictions`, clé de partition `/fileName`) :

```json
{"id": "ferme_1.csv-0x8DD0F3A1B2C4E5F-1", "fileName": "ferme_1.csv", "etag": "\"0x8DD0F3A1B2C4E5F\"", "row": 1, "field_id": "F1-parcelle-nord",
 "inputs": {"N": 90, "P": 42, "K": 43, "temperature": 20.9, "humidity": 82, "ph": 6.5, "rainfall": 202.9},
 "status": "ok", "crop": "rice", "confidence": 0.97, "model_version": "1.0.0", "latency_ms": 3.2,
 "processed_at": "2026-11-24T09:17:44+00:00"}
```

## Choix et robustesse

- **Erreurs** : 422 = donnée invalide, la ligne est enregistrée `status: "invalid"` ; toute autre erreur (5xx, délai, API injoignable) fait échouer le message, qui est réessayé (`maxDequeueCount: 5` dans `host.json`) puis envoyé automatiquement dans `inference-requests-poison`.
- **Idempotence** : `id` = nom du blob + ETag + numéro de ligne, écrit en upsert → un message rejoué remplace au lieu de dupliquer ; un nouveau dépôt du même fichier (nouvel ETag) donne de nouvelles inférences.
- **Performance** : un seul client HTTP (`requests.Session()`) créé une fois et réutilisé pour toutes les lignes.
- **Démarrage à froid** : l'API sur Container Apps descend à 0 ; délai d'attente de 30 s (`API_TIMEOUT_SECONDS`).
- **Secrets** : aucun dans le dépôt. Connexions par identité managée ou réglages de la Function App. `local.settings.json` est ignoré par Git.

## Réglages de la Function App

| Réglage | Valeur | Remarque |
| --- | --- | --- |
| `AGRI_STORAGE__blobServiceUri` | `https://<compte>.blob.core.windows.net` | identité managée (recommandé) |
| `AGRI_STORAGE__queueServiceUri` | `https://<compte>.queue.core.windows.net` | identité managée |
| `AGRI_STORAGE` | chaîne de connexion | seulement si pas d'identité managée (réglage, jamais Git) |
| `API_URL` | URL Container Apps de A (fausse API au début) | |
| `COSMOS_ENDPOINT` | `https://<compte>.documents.azure.com:443/` | |
| `COSMOS_DATABASE` / `COSMOS_CONTAINER` | `agrireco` / `predictions` | |
| `COSMOS_KEY` | clé Cosmos | seulement si pas de rôle RBAC Cosmos |
| `QUEUE_NAME` | `inference-requests` | file poison : `inference-requests-poison` (automatique) |

Rôles pour l'identité managée de la Function App : *Storage Blob Data Owner*, *Storage Queue Data Contributor* sur le compte de stockage ; *Cosmos DB Built-in Data Contributor* (rôle de données Cosmos, via `az cosmosdb sql role assignment create`).

## Mise en place dans Azure (avec C)

Remplacer `<...>` ; une seule région, celle choisie par C.

```bash
# 1. Conteneurs et site statique (compte de stockage du projet)
az storage container create --account-name <st> -n input  --auth-mode login
az storage container create --account-name <st> -n rejected --auth-mode login
az storage container create --account-name <st> -n output --auth-mode login
az storage blob service-properties update --account-name <st> --static-website --index-document index.html --auth-mode login

# 2. Cosmos DB (créé par C en Free Tier) : base + conteneur
az cosmosdb sql database create -g <rg> -a <cosmos> -n agrireco
az cosmosdb sql container create -g <rg> -a <cosmos> -d agrireco -n predictions --partition-key-path /fileName

# 3. Event Grid -> dispatcher (après le premier déploiement des fonctions)
#    filtré : BlobCreated uniquement, dossier input/ uniquement
az eventgrid event-subscription create -n evt-input-dispatcher \
  --source-resource-id $(az storage account show -g <rg> -n <st> --query id -o tsv) \
  --endpoint-type azurefunction \
  --endpoint $(az functionapp show -g <rg> -n <func> --query id -o tsv)/functions/dispatcher \
  --included-event-types Microsoft.Storage.BlobCreated \
  --subject-begins-with /blobServices/default/containers/input/

# 4. CORS : UNIQUEMENT l'adresse du site statique (pas de *)
SITE=$(az storage account show -g <rg> -n <st> --query primaryEndpoints.web -o tsv)
az functionapp cors add -g <rg> -n <func> --allowed-origins ${SITE%/}

# 5. Publier le tableau de bord (après avoir mis l'URL de recent dans web/config.js)
az storage blob upload-batch --account-name <st> -d '$web' -s web --overwrite --auth-mode login
```

## Tester en local (sans Azure, sans frais)

```bash
pip install -r functions/requirements.txt -r tools/fake_api/requirements.txt azure-storage-queue pytest
npm install -g azurite

# tests unitaires (validation, 422, rejeu, contrat de l'API)
cd functions && python -m pytest -q && cd ..

# bout en bout : Azurite + fausse API + nos 3 fonctions
azurite --silent --location .azurite &
(cd tools/fake_api && uvicorn main:app --port 8000 &)
python tools/local_e2e.py           # 3 fermes -> file -> résultats ; fichier cassé -> rejected/
python tools/local_e2e.py --fail    # API coupée -> 5 essais -> file poison

# tableau de bord en local : http://localhost:7071/
python tools/local_recent_server.py
```

Avec les Azure Functions Core Tools installées, on peut aussi lancer le vrai runtime : copier `functions/local.settings.json.example` en `local.settings.json`, puis `cd functions && func start`.

## Démo

1. Déposer `samples/ferme_1_riziere.csv`, `ferme_2_plaine.csv`, `ferme_3_verger.csv` dans `input/` → les recommandations apparaissent seules sur le tableau de bord (la ligne `F3-erreur-labo` avec pH = 20 apparaît « invalide »).
2. Déposer `samples/fichier_casse.csv` → il part dans `rejected/` avec la raison (rejeté par le dispatcher, aucun message n'arrive dans la file).
3. File poison : mettre volontairement une mauvaise `API_URL` dans les réglages de la Function App, déposer un fichier **valide** → 5 tentatives dans les journaux, puis le message apparaît dans `inference-requests-poison`. **Remettre la bonne `API_URL` après la démo.**
