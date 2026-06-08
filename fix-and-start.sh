#!/usr/bin/env bash
# Force-recreate containers so API port 8787 is published (fixes blank Port in Docker Desktop).
set -e
cd "$(dirname "$0")"

echo "=== Stopping and removing old WooCommerce enrichment containers ==="
docker stop woocommerceproductenrichment-api-1 woocommerceproductenrichment-ui-1 2>/dev/null || true
docker rm -f woocommerceproductenrichment-api-1 woocommerceproductenrichment-ui-1 2>/dev/null || true
docker compose down --remove-orphans 2>/dev/null || true

if [ ! -f .env ]; then
  echo ""
  echo "ERROR: Create .env first:"
  echo "  cp .env.example .env"
  echo "  # then add DEEPSEEK_API_KEY=..."
  exit 1
fi

echo ""
echo "=== Building and starting (API port 8787 -> container 8000) ==="
docker compose up -d --build --force-recreate

echo ""
echo "=== Waiting for API ==="
for i in $(seq 1 40); do
  if curl -sf http://127.0.0.1:8787/health >/dev/null 2>&1; then
    echo "OK: http://localhost:8787/health"
    curl -s http://127.0.0.1:8787/health
    echo ""
    break
  fi
  sleep 2
  if [ "$i" -eq 40 ]; then
    echo "FAILED: API not responding on port 8787"
    docker logs woocommerceproductenrichment-api-1 --tail 40
    exit 1
  fi
done

echo ""
docker ps --filter name=woocommerceproductenrichment --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo ""
echo "=== Ready ==="
echo "  API:  http://localhost:8787/health"
echo "  UI:   http://localhost:8501"
echo ""
echo "In Docker Desktop, api-1 must show: 8787:8000"
