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


# Run migrations for web server
echo "🌐 Web server detected, running migrations..."
wait_for_db
python manage.py makemigrations || echo "✅ makemigrations done"
python manage.py migrate --fake-initial || echo "✅ migrate --fake-initial done"
python manage.py migrate --run-syncdb || echo "✅ migrate --run-syncdb done"
python manage.py migrate || echo "✅ migrate done"
python manage.py collectstatic --noinput || echo "✅ collectstatic done"
echo "🚀 Starting web server..."
exec uvicorn config.asgi:application --host 0.0.0.0 --port 8000


