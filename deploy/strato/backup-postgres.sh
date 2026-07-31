#!/usr/bin/env bash
set -euo pipefail

backup_directory=/var/backups/vitamine-cloud/postgres
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
temporary_path="$backup_directory/vitamine-$timestamp.dump.partial"
final_path="$backup_directory/vitamine-$timestamp.dump"

umask 077
/usr/bin/pg_dump --dbname=vitamine --format=custom --file="$temporary_path"
/usr/bin/mv "$temporary_path" "$final_path"
/usr/bin/find "$backup_directory" -type f -name 'vitamine-*.dump' -mtime +14 -delete
