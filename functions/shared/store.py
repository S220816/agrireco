"""Accès au stockage : Blob (input/, rejected/, output/) et Cosmos DB.

Aucun secret dans le code : tout vient des réglages de la Function App
(ou de local.settings.json en local, fichier ignoré par Git).

Blob :
  - AGRI_STORAGE                 chaîne de connexion (local / Azurite), OU
  - AGRI_STORAGE__blobServiceUri https://<compte>.blob.core.windows.net (identité managée)

Cosmos DB :
  - COSMOS_ENDPOINT + identité managée (rôle « Cosmos DB Built-in Data Contributor »),
    ou COSMOS_ENDPOINT + COSMOS_KEY (réglage de l'app, jamais dans Git)
  - COSMOS_DATABASE (défaut pipeline), COSMOS_CONTAINER (défaut inferences)
  - sans COSMOS_ENDPOINT : stockage local dans un fichier JSON (développement)
"""
import json
import os
import threading
from datetime import datetime, timezone

INPUT_CONTAINER = os.getenv("INPUT_CONTAINER", "input")
REJECTED_CONTAINER = os.getenv("REJECTED_CONTAINER", "rejected")
OUTPUT_CONTAINER = os.getenv("OUTPUT_CONTAINER", "output")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- Blob
_blob_service = None


def blob_service():
    global _blob_service
    if _blob_service is None:
        from azure.storage.blob import BlobServiceClient

        conn = os.getenv("AGRI_STORAGE")
        if conn:
            _blob_service = BlobServiceClient.from_connection_string(conn)
        else:
            from azure.identity import DefaultAzureCredential

            _blob_service = BlobServiceClient(
                os.environ["AGRI_STORAGE__blobServiceUri"], credential=DefaultAzureCredential()
            )
    return _blob_service


def read_blob(container: str, name: str, etag: str | None = None) -> bytes | None:
    """Lit un blob. Avec etag : renvoie None si le blob a été remplacé depuis (autre version)."""
    client = blob_service().get_blob_client(container, name)
    if not etag:
        return client.download_blob().readall()
    from azure.core import MatchConditions
    from azure.core.exceptions import ResourceModifiedError

    try:
        return client.download_blob(etag=etag, match_condition=MatchConditions.IfNotModified).readall()
    except ResourceModifiedError:
        return None


def write_blob(container: str, name: str, data: bytes, content_type: str = "text/csv") -> None:
    from azure.storage.blob import ContentSettings

    blob_service().get_blob_client(container, name).upload_blob(
        data, overwrite=True, content_settings=ContentSettings(content_type=content_type)
    )


def move_to_rejected(name: str, data: bytes, reason: str) -> None:
    """Copie le fichier dans rejected/ avec la raison, puis le retire de input/."""
    write_blob(REJECTED_CONTAINER, name, data)
    write_blob(REJECTED_CONTAINER, f"{name}.reason.txt", reason.encode("utf-8"), "text/plain")
    try:
        blob_service().get_blob_client(INPUT_CONTAINER, name).delete_blob()
    except Exception:  # déjà supprimé : rien à faire
        pass


# ---------------------------------------------------------------- Résultats
class LocalStore:
    """Remplace Cosmos DB en local : un fichier JSON (upsert par id)."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def upsert(self, doc: dict) -> None:
        with self.lock:
            data = self._load()
            data[doc["id"]] = doc
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)

    def recent(self, limit: int) -> list[dict]:
        docs = list(self._load().values())
        docs.sort(key=lambda d: d.get("processed_at", ""), reverse=True)
        return docs[:limit]


class CosmosStore:
    """Résultats dans Cosmos DB (clé de partition : /fileName)."""

    def __init__(self):
        from azure.cosmos import CosmosClient

        endpoint = os.environ["COSMOS_ENDPOINT"]
        key = os.getenv("COSMOS_KEY")
        if key:
            client = CosmosClient(endpoint, credential=key)
        else:
            from azure.identity import DefaultAzureCredential

            client = CosmosClient(endpoint, credential=DefaultAzureCredential())
        db = client.get_database_client(os.getenv("COSMOS_DATABASE", "pipeline"))
        self.container = db.get_container_client(os.getenv("COSMOS_CONTAINER", "inferences"))

    def upsert(self, doc: dict) -> None:
        self.container.upsert_item(doc)

    def recent(self, limit: int) -> list[dict]:
        query = (
            "SELECT TOP @n c.id, c.fileName, c.field_id, c.crop, c.confidence, c.status, "
            "c.model_version, c.latency_ms, c.processed_at, c.error, c.etag, c.row FROM c ORDER BY c.processed_at DESC"
        )
        return list(
            self.container.query_items(
                query, parameters=[{"name": "@n", "value": limit}], enable_cross_partition_query=True
            )
        )


_store = None


def results_store():
    global _store
    if _store is None:
        if os.getenv("COSMOS_ENDPOINT"):
            _store = CosmosStore()
        else:
            _store = LocalStore(os.getenv("LOCAL_STORE_PATH", "local_results.json"))
    return _store
