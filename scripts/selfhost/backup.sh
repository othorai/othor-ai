#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Othor AI self-host backup (M20.4; docs/self-host.md "Backups"). Run from the folder that holds
# compose.selfhost.yml and .env:
#   scripts/selfhost/backup.sh [target folder]      (default ./backups)
# Writes <target>/othor-<UTC time>/: db.dump (pg_dump, custom format) and data.tar.gz (the
# /data volume: results, files, the instance key). Keep them somewhere off this server; the
# instance key decrypts source credentials, so treat a backup like a password.
set -euo pipefail
umask 077  # the backup holds the instance key: owner-only files

target="${1:-./backups}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dir="$target/othor-$stamp"
compose=(docker compose -f compose.selfhost.yml)

mkdir -p "$dir"
chmod 700 "$dir"
echo "database → $dir/db.dump"
"${compose[@]}" exec -T db pg_dump -U othor -d othor --format=custom > "$dir/db.dump"
echo "data volume → $dir/data.tar.gz"
"${compose[@]}" run --rm --no-deps -T --entrypoint tar web -czf - -C /data . > "$dir/data.tar.gz"
ls -l "$dir"
echo "done. Restore: docs/self-host.md, Backups."
