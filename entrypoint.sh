#!/bin/bash
set -e

echo "🔥 Starting deployment..."

if [[ "$1" != "celery" ]] && [[ "$1" != "beat" ]] && [[ "$1" != "flower" ]]; then
    echo "📦 Running migrations..."
    python manage.py migrate --noinput
    
    echo "📁 Collecting static..."
    python manage.py collectstatic --noinput
    
    echo "✅ Done!"
fi

exec "$@"

# #!/bin/bash
# set -e

# echo "🔥 Starting deployment process..."

# wait_for_db() {
#     echo "⏰ Waiting for PostgreSQL..."
    
#     until PGPASSWORD=$POSTGRES_PASSWORD psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\q' 2>/dev/null; do
#         echo "PostgreSQL is unavailable - sleeping"
#         sleep 2
#     done
    
#     echo "✅ PostgreSQL is up and ready!"
# }

# if [[ "$1" != "celery" ]] && [[ "$1" != "beat" ]] && [[ "$1" != "flower" ]]; then
#     wait_for_db
    
#     echo "📦 Running migrations..."
#     python manage.py makemigrations --noinput || true
#     python manage.py migrate --noinput || true
    
#     echo "📁 Collecting static files..."
#     python manage.py collectstatic --noinput || true
    
#     echo "✅ Setup complete!"
# fi

# echo "🚀 Starting application: $@"
# exec "$@"

