# Server Deployment Guide

This guide is for deploying the product enrichment app on a Linux server so the user experience is just:

```text
Open URL -> upload WooCommerce CSV -> choose category -> enrich -> download CSV
```

The server runs Docker. The person using the app only needs a browser.

---

## 1. What You Need

- A private GitHub repo containing this project
- A Linux server with Docker and Docker Compose installed
- A domain or subdomain, for example `product-enrichment.company.com`
- A DeepSeek API key
- Ports `80` and `443` open on the server firewall

Recommended server size for small/medium batches:

- 2 CPU
- 4 GB RAM
- 20+ GB disk

Large batches mostly depend on API time and cost, not CPU.

---

## 2. Put The Code In A Private Repo

From your local machine:

```bash
git init
git add .
git commit -m "Initial product enrichment app"
git branch -M main
git remote add origin <private-repo-url>
git push -u origin main
```

Do not commit `.env`. It is ignored by `.gitignore`.

---

## 3. Point The Domain To The Server

In your DNS provider, create an `A` record:

```text
product-enrichment.company.com -> SERVER_PUBLIC_IP
```

Caddy will automatically request and renew HTTPS certificates after DNS points to the server.

---

## 4. Prepare The Server

SSH into the server:

```bash
ssh user@SERVER_PUBLIC_IP
```

Install Docker if it is not already installed. On Ubuntu, the simplest approach is usually Docker's official install script:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in so the Docker group change applies.

Confirm Docker works:

```bash
docker --version
docker compose version
```

---

## 5. Deploy The App

Clone the private repo:

```bash
git clone <private-repo-url>
cd "Woocommerce product enrichment"
```

Create the environment file:

```bash
cp .env.example .env
nano .env
```

Set these values:

```env
APP_DOMAIN=product-enrichment.company.com
DEEPSEEK_API_KEY=sk-your-real-key
QA_MODE=standard
```

Use `QA_MODE=standard` for normal faster batches. Use `QA_MODE=strict` when you want extra AI review/readiness passes and are comfortable with slower runtime and higher API cost.

Start the production stack:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Open:

```text
https://product-enrichment.company.com
```

---

## 6. Daily Use

The user workflow is:

1. Open the app URL.
2. Upload a WooCommerce CSV export.
3. Choose the correct product category.
4. Start with 1 row for a test.
5. Click **Enrich Products**.
6. Review the live preview.
7. Download the enriched CSV.
8. Import it back into WooCommerce.

---

## 7. Updating The App

On the server:

```bash
cd "Woocommerce product enrichment"
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

---

## 8. Stopping Or Restarting

Stop:

```bash
docker compose -f docker-compose.prod.yml down
```

Restart:

```bash
docker compose -f docker-compose.prod.yml up -d
```

View running services:

```bash
docker compose -f docker-compose.prod.yml ps
```

View logs:

```bash
docker compose -f docker-compose.prod.yml logs -f
```

---

## 9. Security Notes

- Keep the repo private.
- Never commit `.env` or API keys.
- Use HTTPS only; Caddy handles this automatically when DNS is correct.
- Uploaded CSVs are stored in the Docker volume used by the API/UI containers.
- If CSVs contain sensitive data, clear old uploads periodically.

Clear generated uploads:

```bash
docker compose -f docker-compose.prod.yml down
docker volume rm woocommerce-enrichment_uploads_data
docker compose -f docker-compose.prod.yml up -d
```

Only run the volume removal command when you are sure no one needs old outputs.

---

## 10. Troubleshooting

### The URL does not load

Check DNS:

```bash
dig product-enrichment.company.com
```

Check containers:

```bash
docker compose -f docker-compose.prod.yml ps
```

Check logs:

```bash
docker compose -f docker-compose.prod.yml logs -f caddy
docker compose -f docker-compose.prod.yml logs -f ui
docker compose -f docker-compose.prod.yml logs -f api
```

### HTTPS certificate does not issue

Confirm:

- DNS points to the server public IP
- Ports `80` and `443` are open
- `APP_DOMAIN` in `.env` matches the domain exactly

Then restart:

```bash
docker compose -f docker-compose.prod.yml restart caddy
```

### Categories do not load

Check the API health from inside Docker:

```bash
docker compose -f docker-compose.prod.yml exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```

### Enrichment fails

Most common causes:

- Missing or invalid `DEEPSEEK_API_KEY`
- Wrong product category selected
- CSV missing SKU/name/attribute columns
- API credits exhausted

Start with a 1-row test before running a full catalog.

---

## 11. Current Limitation

This deployment keeps the existing app architecture. It is good for a small internal tool, but for heavier multi-user use the next upgrade should be job isolation and background processing so multiple users/runs cannot overwrite each other.
