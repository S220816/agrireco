# agrireco
Un agriculteur dépose l'analyse de son sol, et le système lui dit automatiquement quelle culture planter , en moins d'une minute.

## Partie B — pipeline (Sarah)

Dépôt d'un CSV dans `input/` → **Event Grid** (BlobCreated, filtré sur `input/`) → `dispatcher` → file `inference-requests` → `worker` (API de prédiction, Cosmos DB, `output/`) → `GET /api/recent` → tableau de bord. Échecs répétés → `inference-requests-poison`.

**Pourquoi le déclencheur Event Grid ?** L'événement contient le nom du blob, son ETag et sa taille, exactement ce que le message de la file doit porter, et l'abonnement se crée vers la fonction sans URL ni clé. L'abonnement est filtré sur `Microsoft.Storage.BlobCreated` et sur le dossier `input/`.

Détails, réglages et commandes Azure : [docs/PIPELINE.md](docs/PIPELINE.md).
