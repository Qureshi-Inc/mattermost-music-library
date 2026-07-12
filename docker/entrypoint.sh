#!/bin/bash
set -e

echo "Starting Slaptastic music importer..."

# Ensure data and music directories exist and are writable
mkdir -p /app/data /app/music 2>/dev/null || true

# Run database migrations if alembic is available
if command -v alembic &> /dev/null && [ -f alembic.ini ]; then
    echo "Running database migrations..."
    alembic upgrade head
fi

# Start the application with the command passed in (defaults to uvicorn via CMD)
echo "Starting application..."
exec "$@"
