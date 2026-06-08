#!/usr/bin/env bash
# Start WooCommerce Product Enrichment (API + UI)
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Missing .env — run: cp .env.example .env"
  echo "Then add your DEEPSEEK_API_KEY to .env"
  exit 1
fi

echo "Stopping old containers..."
docker compose down --remove-orphans 2>/dev/null || true

echo "Starting API (woocommerceproductenrichment-api-1) and UI..."
docker compose up -d --build

echo ""
echo "Waiting for API health check..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8787/health >/dev/null 2>&1; then
    echo "API is healthy."
    break
  fi
  sleep 2
  if [ "$i" -eq 30 ]; then
    echo "API did not respond on http://127.0.0.1:8787/health"
    echo "Check logs: docker logs woocommerceproductenrichment-api-1"
    exit 1
  fi
done

echo ""
echo "Ready:"
echo "  Web UI:  http://localhost:8501"
echo "  API:     http://localhost:8787/health"
echo "           http://localhost:8787/categories"
echo ""
docker compose ps
