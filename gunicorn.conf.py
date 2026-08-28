"""gunicorn config. Tune GUNICORN_* env vars per deployment."""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# Sync workers. The skin sandbox uses a per-process ThreadPoolExecutor, so
# threads > 1 also help there; default to a small thread count.
workers = int(os.environ.get("GUNICORN_WORKERS", 2 * multiprocessing.cpu_count() + 1))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")

timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))
graceful_timeout = 30
keepalive = 5
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = 100

# Trust the X-Forwarded-* headers from the edge proxy only.
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "*")

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
