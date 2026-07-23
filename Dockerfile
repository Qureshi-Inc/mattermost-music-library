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
    && apt-get install -y --no-install-recommends ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno — required JS runtime for yt-dlp's YouTube extractor. Without it, newer
# YouTube videos fail extraction with HTTP 403 (see yt-dlp EJS wiki).
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && /usr/local/bin/deno --version

RUN useradd --create-home --shell /bin/bash slaptastic

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

RUN mkdir -p /app/data /app/music

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
