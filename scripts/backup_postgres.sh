#!/bin/bash
# PostgreSQL hourly backup script for the tracker database

BACKUP_DIR="/var/www/tracker/backups/postgres"
DB_NAME="tracker"
DB_USER="tracker_user"
DB_HOST="localhost"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/${DB_NAME}_${TIMESTAMP}.sql.gz"

# Keep backups for this many days before pruning
RETENTION_DAYS=7

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

# Source the DB password from the secrets file instead of hardcoding it.
SECRETS_FILE="/var/www/tracker/.secrets.env"
if [[ -r "${SECRETS_FILE}" ]]; then
    set -a; . "${SECRETS_FILE}"; set +a
fi
if [[ -z "${DATABASE_URL}" ]]; then
    echo "[$(date)] Backup FAILED: DATABASE_URL not set (expected in ${SECRETS_FILE})" >&2
    exit 1
fi
# Extract the password component from postgresql://user:PASSWORD@host/db
PGPASSWORD="$(printf '%s' "${DATABASE_URL}" | sed -n 's#^[^:]*://[^:]*:\([^@]*\)@.*#\1#p')"

# Run pg_dump and compress output
PGPASSWORD="${PGPASSWORD}" pg_dump \
    -h "${DB_HOST}" \
    -U "${DB_USER}" \
    -d "${DB_NAME}" \
    --format=plain \
    --no-password \
    | gzip > "${BACKUP_FILE}"

if [[ $? -eq 0 ]]; then
    echo "[$(date)] Backup succeeded: ${BACKUP_FILE}"
else
    echo "[$(date)] Backup FAILED for database ${DB_NAME}" >&2
    rm -f "${BACKUP_FILE}"
    exit 1
fi

# Snapshot .secrets.env alongside the dump. The DB dump alone is NOT restorable:
# .secrets.env holds the DB password, the M365 secret, and SETTINGS_ENCRYPTION_KEY
# (the only key that can decrypt the encrypted Setting values in the dump).
# Kept perms-600; secure whatever storage backups/ syncs to.
if [[ -r "${SECRETS_FILE}" ]]; then
    install -m 600 "${SECRETS_FILE}" "${BACKUP_DIR}/secrets.env.snapshot"
    echo "[$(date)] Snapshotted .secrets.env -> ${BACKUP_DIR}/secrets.env.snapshot (mode 600)"
fi

# Prune backups older than RETENTION_DAYS
find "${BACKUP_DIR}" -name "${DB_NAME}_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

echo "[$(date)] Pruned backups older than ${RETENTION_DAYS} days from ${BACKUP_DIR}"
