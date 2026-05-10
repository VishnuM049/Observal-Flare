#!/bin/bash
set -e

echo "Running database migrations..."
alembic -c server/alembic.ini upgrade head

echo "Starting application..."
exec "$@"
