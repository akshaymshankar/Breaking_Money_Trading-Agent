#!/usr/bin/env bash
# Installs everything without opening the app. start-trading.command does this too.
cd "$(dirname "$0")" && exec ./start-trading.command --setup-only "$@"
