#!/bin/sh
# Cron entrypoint: every writable file stays under /vol1.
set -eu
export TMPDIR=/vol1/maijev/tmp
export TMP=/vol1/maijev/tmp
export TEMP=/vol1/maijev/tmp
export UV_CACHE_DIR=/vol1/maijev/cache
mkdir -p /vol1/maijev/logs "$TMPDIR"
log=/vol1/maijev/logs/auto-publish.log
if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt 10485760 ]; then
    mv -f "$log" "$log.1"
fi
cd /vol1/maijev/app
/vol1/maijev/venv/bin/python -m flows.maijev.auto_publish run >> "$log" 2>&1
