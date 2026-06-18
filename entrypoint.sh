#!/bin/bash
set -o pipefail

echo "🔥 FORCING DEPLOYMENT TO WORK..."

wait_for_db() {
    echo "⏰ Waiting for database..."
    max_attempts=30
    attempt=1

    while [ $attempt -le $max_attempts ]; do
        if python manage.py check --database default > /dev/null 2>&1; then
            echo "✅ Database ready!"
            break
        else
            sleep 2
            attempt=$((attempt + 1))
        fi

        if [ $attempt -gt $max_attempts ]; then
            echo "❌ Database timeout"
            exit 1
        fi
    done
}

wait_for_db

# Only run migrations/collectstatic for web server
if [[ "$1" == "uvicorn" ]] || [[ "$1" == "gunicorn" ]] || [[ -z "$1" ]]; then
    echo "🌐 Web server detected, running migrations..."
    python manage.py migrate --fake-initial || true
    python manage.py migrate --run-syncdb || true
    python manage.py migrate || true
    python manage.py collectstatic --noinput || true
    echo "🚀 Starting web server..."
    exec uvicorn config.asgi:application --host 0.0.0.0 --port 8000
else
    echo "🚀 Starting: $@"
    exec "$@"
fi