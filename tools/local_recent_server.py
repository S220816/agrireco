"""Petit serveur local qui expose la fonction recent sur http://localhost:7071/api/recent
(remplace le runtime Azure Functions quand on n'a pas les Core Tools) et sert web/.
Usage : python tools/local_recent_server.py  puis ouvrir http://localhost:7071/
"""
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "functions"))
os.environ.setdefault("LOCAL_STORE_PATH", str(ROOT / "functions" / "local_results.json"))
import azure.functions as func  # noqa: E402
import function_app  # noqa: E402

RECENT = {f.get_function_name(): f for f in function_app.app.get_functions()}["recent"].get_user_function()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT / "web"), **kw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/recent":
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            resp = RECENT(func.HttpRequest("GET", self.path, params=params, body=b""))
            body = resp.get_body()
            self.send_response(resp.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 7071), Handler).serve_forever()
