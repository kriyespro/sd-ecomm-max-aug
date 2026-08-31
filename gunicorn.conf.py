"""gunicorn config. Tune GUNICORN_* env vars per deployment."""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# gthread workers: real concurrency is workers * threads. Django here is
# I/O-bound (DB, Redis, upstream), so a few threads per worker beat more
# processes and cost far less RAM.
#
# multiprocessing.cpu_count() reports the HOST core count and ignores the
# cgroup/VPS limit, so an unbounded 2*cpu+1 spawns a dozen+ 200 MB workers on a
# small box and drives it into swap. Cap the auto value; override per box with
# GUNICORN_WORKERS / GUNICORN_THREADS.
_auto_workers = min(2 * multiprocessing.cpu_count() + 1, 5)
workers = int(os.environ.get("GUNICORN_WORKERS", _auto_workers))
threads = int(os.environ.get("GUNICORN_THREADS", 4))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")

timeout = int(os.environ.get("GUNICORN_TIMEOUT", 30))
graceful_timeout = 30
keepalive = 5
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = 100

# Load the app in the master, then fork workers — the Python heap (Django,
# Pillow, compiled templates) is shared copy-on-write, so N workers cost far
# less than N full interpreters. Safe here: no DB/socket is opened at import
# (connections are lazy and per-worker; CONN_MAX_AGE handles reuse).
preload_app = os.environ.get("GUNICORN_PRELOAD", "true").lower() != "false"
# gthread scratch space on tmpfs, not the (possibly disk-backed) default.
worker_tmp_dir = "/dev/shm"

# Trust the X-Forwarded-* headers from the edge proxy only.
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "*")

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
