#!/usr/bin/env bash
# Build both Docker images for the GitBug-Java EvoSuite experiment.
# Run this once before running the batch script with --java-versions.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/2] Building gitbug-java8 ..."
docker build -t gitbug-java8 -f "$SCRIPT_DIR/java8.Dockerfile" "$SCRIPT_DIR"

echo "[2/2] Building gitbug-java11 ..."
docker build -t gitbug-java11 -f "$SCRIPT_DIR/java11.Dockerfile" "$SCRIPT_DIR"

echo ""
echo "Done. Images available:"
docker images | grep "gitbug-java"
