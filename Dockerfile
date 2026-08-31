# ==============================================================================
# Stage 1: Build & Dependencies
# ==============================================================================
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ==============================================================================
# Stage 2: Runtime Image with Unprivileged User & Media Tools
# ==============================================================================
FROM python:3.12-slim-bookworm AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:/app:${PATH}" \
    PYTHONPATH="/install/lib/python3.12/site-packages:/app"

# Install runtime dependencies: FFmpeg, atomic parsers, curl, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy python packages from builder
COPY --from=builder /install /install

# Create non-root unprivileged bot user with dedicated UID/GID
RUN groupadd -g 10001 botuser && \
    useradd -u 10001 -g botuser -s /bin/bash -m botuser

# Create downloads temporary storage with correct permissions
RUN mkdir -p /tmp/downloads && \
    chown -R botuser:botuser /tmp/downloads /app

# Copy application source code
COPY --chown=botuser:botuser . /app

USER botuser

# Health check to ensure python process is responsive
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python.*main.py" > /dev/null || exit 1

ENTRYPOINT ["python", "main.py"]
