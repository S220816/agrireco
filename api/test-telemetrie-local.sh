#!/bin/bash
# Reproduit en local les conditions d'Azure Container Apps (0,25 vCPU, 0,5 Gio)
# et mesure le temps avant que /health reponde, avec la vraie chaine de connexion.
# Usage : APPLICATIONINSIGHTS_CONNECTION_STRING='InstrumentationKey=...' ./test-telemetrie-local.sh
# La chaine n'est jamais ecrite dans un fichier : elle reste dans le terminal.
set -u
cd "$(dirname "$0")"
docker rm -f agri-test >/dev/null 2>&1
echo "Construction de l'image (amd64, comme sur Azure)..."
docker build -q --platform linux/amd64 -t agrireco-api:test . >/dev/null || exit 1
start=$(date +%s)
docker run -d --name agri-test --platform linux/amd64 --cpus 0.25 --memory 512m -p 8001:8000 \
  -e APPLICATIONINSIGHTS_CONNECTION_STRING agrireco-api:test >/dev/null || exit 1
for i in $(seq 1 120); do
  if curl -fs http://localhost:8001/health -o /tmp/agri-health.json; then
    echo "OK : /health repond apres $(( $(date +%s) - start )) s"; cat /tmp/agri-health.json; echo; break
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' agri-test)" != "true" ]; then
    echo "ECHEC : le conteneur s'est arrete"; break
  fi
  sleep 1
done
echo "--- Memoire et CPU ---"; docker stats --no-stream agri-test
echo "--- 20 dernieres lignes de journal ---"; docker logs --tail 20 agri-test
docker rm -f agri-test >/dev/null
