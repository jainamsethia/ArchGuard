#!/bin/sh
# Bring the schema to head, then hand off to the real command.
#
# Migrations run here rather than from application code on purpose. Called at
# startup inside the app, every replica races to migrate the same database on
# every deploy, and a failure surfaces as a half-started server rather than a
# failed release. Here it is one step, before anything serves traffic, and a
# non-zero exit stops the rollout.
#
# Idempotent: `alembic upgrade head` on an up-to-date database is a no-op, so
# restarts and multiple replicas are safe -- Alembic takes a lock on
# alembic_version for the duration.
set -e

if [ -n "${DATABASE_URL}" ]; then
    echo "Running database migrations..."
    alembic upgrade head
else
    # Refusing here would break `docker run <image> python -c ...`, which the
    # build checks use. The application's own startup check is what refuses to
    # serve production traffic without a database.
    echo "DATABASE_URL is not set; skipping migrations." >&2
fi

exec "$@"
