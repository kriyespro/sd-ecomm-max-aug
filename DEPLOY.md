# Deployment (Docker)

## What's in the box

| file | purpose |
|---|---|
| `Dockerfile` | `python:3.13-slim`, non-root `app` user, gunicorn, container `HEALTHCHECK` |
| `docker-compose.yml` | `web` + `worker` + `beat` + `db` (pg 16) + `redis` 7 + `pgbackup`; volumes `pgdata` / `media` / `pgbackups` |
| `docker/entrypoint.sh` | waits for db → `migrate` → `collectstatic` → exec gunicorn |
| `config/celery.py` | Celery app; tasks autodiscovered from each app's `tasks.py` |
| `gunicorn.conf.py` | gthread workers, timeout, `max_requests` recycling |
| `requirements-prod.txt` | `requirements.txt` + psycopg / redis / gunicorn / whitenoise |
| `config/settings/production.py` | DEBUG off, Redis cache, WhiteNoise static, stdout logging, HSTS/SSL |

`config/wsgi.py` and `config/asgi.py` already default to `config.settings.production`.

## First deploy

```sh
cp .env.example .env         # then fill DJANGO_SECRET_KEY + POSTGRES_PASSWORD
#  DJANGO_SECRET_KEY: python -c "import secrets; print(secrets.token_urlsafe(64))"
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

App on `http://localhost:8000` (or `WEB_PORT`). `/healthz/` (liveness),
`/readyz/` (db + cache).

## nginx — mnxstore.com on 159.195.57.98

`.env.prod` sets `WEB_PORT=8888`, so the `web` container publishes on
`:8888` (reachable directly at `http://159.195.57.98:8888` while testing) and
nginx reverse-proxies the real hostnames to it.

```sh
# on the server, after `docker compose up -d`
apt install -y nginx
cp deploy/nginx/mnxstore.conf /etc/nginx/sites-available/mnxstore.conf
ln -sf /etc/nginx/sites-available/mnxstore.conf /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

`server_name` covers `mnxstore.com`, `www`, `*.mnxstore.com` (store
subdomains) and the bare IP; every other Host gets `444`. `client_max_body_size
32m` clears the 30 MB skin-zip cap. `/media/` is served straight off the
`sd-commerce_media` volume — confirm the path with
`docker volume inspect sd-commerce_media`.

DNS: `A mnxstore.com 159.195.57.98`, `A *.mnxstore.com 159.195.57.98` (or
`CNAME www mnxstore.com`).

### TLS (once DNS resolves)

```sh
apt install -y certbot python3-certbot-nginx
certbot --nginx -d mnxstore.com -d www.mnxstore.com
# wildcard *.mnxstore.com needs DNS-01:
#   certbot certonly --manual --preferred-challenges dns -d '*.mnxstore.com' -d mnxstore.com
```

Then flip `.env`: `DJANGO_SECURE_SSL_REDIRECT=true`, drop the `http://IP` lines
from `DJANGO_CSRF_TRUSTED_ORIGINS`, and `docker compose up -d web worker beat`.

## Redis

Two logical DBs on the one Redis instance:
- **db 1** — Django cache → DRF throttle counters (`Anon`/`User` rate throttles,
  shared across gunicorn workers) + `cached_db` sessions.
- **db 0** — Celery broker.

## Celery

`worker` and `beat` containers run off the same image.

| task | trigger |
|---|---|
| `notifications.send_notification_task` | every order/payment/shipment domain event — email/SMS is now off the checkout request path |
| `webhooks.deliver_event_task` | every domain event — HTTP POST to subscriber endpoints, async |
| `webhooks.retry_due_task` | **beat**, every 5 min — retries failed webhook deliveries (exp. backoff) |
| `projects.verify_pending_domains_task` | **beat**, every 15 min — re-checks DNS for unverified custom domains added in the last 14 days |
| `billing.issue_due_invoices_task` | **beat**, every 6 h — issues renewal invoices for periods ending soon |
| `billing.suspend_overdue_task` | **beat**, every 6 h — suspends stores whose invoice is past due + grace |

Dev runs `CELERY_TASK_ALWAYS_EAGER` (inline, `memory://` broker) — no worker
needed. Add a task: drop a `@shared_task` in `apps/<app>/tasks.py`.

## Metrics

`django-prometheus` instruments requests, and `PrometheusAfterMiddleware`
exposes counters. `GET /metrics` returns the Prometheus exposition **only** when
the request carries `X-Metrics-Token: <DJANGO_METRICS_TOKEN>` — otherwise 404.
Point Prometheus at it with that header; chart in Grafana; alert on error rate /
latency / Celery failures. (DB + cache query histograms need the
`django_prometheus.*` DB/cache backends — swap them in `production.py` when you
want that detail.)

## Automated Postgres backups

The `pgbackup` service (`prodrigestivill/postgres-backup-local`) runs `pg_dump`
on `BACKUP_SCHEDULE` (`@daily`) into the `pgbackups` volume with daily / weekly /
monthly retention (`BACKUP_KEEP_*`).

```sh
docker compose exec pgbackup /backup.sh              # on-demand backup
docker compose run --rm -v $PWD:/out pgbackup sh -c 'cp /backups/last/*.sql.gz /out/'   # pull latest
# restore:
gunzip -c dump.sql.gz | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

Ship the `pgbackups` volume off-box (rclone / S3 sync / managed-Postgres
snapshots) — a backup on the same host is not a backup.

## Performance setup

- **DB**: `CONN_MAX_AGE=60` + `CONN_HEALTH_CHECKS` (persistent, self-healing connections).
- **Static**: WhiteNoise serves `/static/` hashed + brotli/gzip, 1-year cache. No nginx needed for static.
- **Workers**: `GUNICORN_WORKERS` defaults to `2*cpu+1`, `GUNICORN_THREADS=2`. `max_requests=1000` recycles workers to cap memory drift.
- **Skin sandbox**: per-process env cache (bounded to 8) + capped `range()` + 2.5s render timeout — safe under multiple workers.

## Media — the one thing that needs a decision

Uploads (product images, skin assets, store logos) are `FileSystemStorage` →
`/app/media`, mounted as the `media` volume. That works for a **single web
replica**. For >1 replica or zero-downtime deploys, move media to object
storage:

```
pip install django-storages boto3          # add to requirements-prod.txt
# .env
DJANGO_DEFAULT_FILE_STORAGE=storages.backends.s3.S3Storage
DJANGO_MEDIA_URL=https://cdn.yourdomain.com/media/
# + AWS_* / bucket env vars per django-storages
```

## Multi-tenant hosts + Cloudflare

Stores connect their own verified custom domains, so `ALLOWED_HOSTS` defaults to
`*`. **The edge proxy must only forward hosts you serve** — that's the real
gate. Put every *platform* admin domain in `DJANGO_CSRF_TRUSTED_ORIGINS` (a
store's own storefront forms are same-origin and need no listing; the API is
token-auth, no CSRF).

**Each store domain via Cloudflare (free plan):**

1. Store owner adds the domain in Mission Control → **Domains**. Screen shows a
   TXT record: `_sd-verify.<host>  →  sd-verify=<token>`.
2. In the domain's Cloudflare DNS:
   - add that **TXT** record (DNS-only),
   - point the host at your origin — `CNAME <host> → app.yourplatform.com`
     (apex: use Cloudflare's CNAME-flattening), proxy **on** (orange cloud).
3. Cloudflare SSL/TLS mode: **Full (strict)** — needs a real cert on the origin
   (Caddy auto-HTTPS, or a Cloudflare Origin CA cert).
4. Back in Mission Control, click **Verify**. `verify_pending_domains_task` also
   re-checks every 15 min, so a late TXT record self-heals.

`dig +short TXT _sd-verify.<host>` (the verification lookup) resolves against
Cloudflare-hosted DNS with no change.

**Origin hardening:** restrict the origin firewall to
[Cloudflare's IP ranges](https://www.cloudflare.com/ips/) so nobody bypasses the
proxy and spoofs `CF-Connecting-IP`. Then set `DJANGO_TRUST_PROXY_HEADERS=1`
(compose does) — `RealClientIPMiddleware` rewrites `REMOTE_ADDR` from
`CF-Connecting-IP`, so throttling / audit logs see the real visitor, not
Cloudflare's edge. Also set `forwarded_allow_ips` (gunicorn) to the CF ranges or
your internal proxy IP.

## Scaling to N replicas

```sh
docker compose up -d --scale web=3
```
Set `RUN_MIGRATIONS=0` on all but one replica (or run migrations as a one-off
`docker compose run --rm web python manage.py migrate`). Put a load balancer /
TLS proxy in front. Move media to S3 first (see above).

## Not included (add when needed)

- CDN in front of static/media
- APM / metrics exporter
- Off-box shipping of the `pgbackups` volume
