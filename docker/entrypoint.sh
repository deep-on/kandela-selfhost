#!/bin/sh
# Fix volume directory ownership then drop to non-root user (gosu).
# Docker managed volumes are created as root, so we chown at startup.
set -e
chown -R memuser:memuser /data/memory_db 2>/dev/null || true
exec gosu memuser "$@"
