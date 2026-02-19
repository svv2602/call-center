# ---- Admin UI build stage ----
FROM node:20-slim AS ui-builder

WORKDIR /ui
COPY admin-ui/package.json admin-ui/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY admin-ui/ ./
RUN npm run build

# ---- Builder stage ----
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies for asyncpg (libpq-dev) and general compilation
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install only production dependencies into /build/venv
RUN python -m venv /build/venv && \
    /build/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /build/venv/bin/pip install --no-cache-dir .

# ---- Runtime stage ----
FROM python:3.12-slim

# libpq is needed at runtime by asyncpg
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home app

WORKDIR /app

# Copy virtualenv from builder and fix shebang paths
COPY --from=builder /build/venv /app/venv
RUN sed -i 's|#!/build/venv/bin/python|#!/app/venv/bin/python|' /app/venv/bin/*
ENV PATH="/app/venv/bin:$PATH"

# Copy application code and resources
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY asterisk/ ./asterisk/
COPY knowledge_base/ ./knowledge_base/
COPY knowledge_seed/ ./knowledge_seed/
COPY --from=ui-builder /ui/dist/ ./admin-ui/dist/

# Install the package itself (src/) — metadata only, deps already in venv
COPY pyproject.toml ./
RUN pip install --no-cache-dir --no-deps .

USER app

EXPOSE 9092 8080

CMD ["python", "-m", "src.main"]
