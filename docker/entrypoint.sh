#!/bin/sh
set -e

# Wait for Postgres (compose starts them together).
if [ -n "$POSTGRES_HOST" ]; then
  echo "waiting for postgres at $POSTGRES_HOST:${POSTGRES_PORT:-5432} ..."
  until python -c "import socket,sys; s=socket.socket(); s.settimeout(2); \
    sys.exit(0) if not s.connect_ex(('$POSTGRES_HOST', int('${POSTGRES_PORT:-5432}'))) else sys.exit(1)"; do
    sleep 1
  done
fi

# Only the first replica should run migrations; RUN_MIGRATIONS=0 to skip.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi

python manage.py collectstatic --noinput --clear

exec "$@"
