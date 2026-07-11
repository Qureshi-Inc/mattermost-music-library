# Build stage
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN pip install --no-cache-dir --upgrade pip

# Copy project files
COPY pyproject.toml ./
COPY app/ ./app/

# Install the package and its dependencies
RUN pip install --no-cache-dir --prefix=/install .

# Runtime stage
FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash slaptastic

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

RUN chown -R slaptastic:slaptastic /app

USER slaptastic

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
