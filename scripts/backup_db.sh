#!/bin/bash
set -e

# ==== কনফিগ ====
DB_CONTAINER="mysportsnest_db"        # docker ps দিয়ে আসল নাম বসান
DB_NAME="mysportsnest_db"                # .env এ POSTGRES_DB চেক করুন
DB_USER="mysportsnest_user"                    # .env এ POSTGRES_USER চেক করুন
BACKUP_DIR="/var/backups/mysportsnest"
KEEP_DAYS=14

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="${BACKUP_DIR}/mysportsnest_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

docker exec -t "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$FILENAME"

find "$BACKUP_DIR" -name "mysportsnest_*.sql.gz" -mtime +$KEEP_DAYS -delete

echo "$(date): Backup completed -> $FILENAME" >> "${BACKUP_DIR}/backup.log"
