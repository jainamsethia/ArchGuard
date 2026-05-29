#!/bin/sh
set -e

# If GITHUB_WORKSPACE is owned by root, git will complain about dubious ownership.
# We must configure git to consider the workspace safe.
git config --global --add safe.directory "$GITHUB_WORKSPACE"

# We are already running as the archguard user (uid 1000) per the Dockerfile USER directive.
# Simply execute the CLI command.
exec archguard "$@"
