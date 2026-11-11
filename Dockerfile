# syntax=docker/dockerfile:1
FROM node:22-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY app/i18n/ /app/i18n/
RUN npm test && npm run build

FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

FROM base AS test
COPY requirements-dev.txt requirements-dev.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY alembic.ini pytest.ini ./
COPY alembic ./alembic
COPY app ./app
COPY tests ./tests
COPY docs ./docs
COPY frontend ./frontend
COPY --from=frontend-builder /app/frontend_dist ./app/frontend_dist
RUN python -m pytest -q

FROM base AS runtime
ARG APP_VERSION=2.2.0
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown 1000:1000 /data
COPY --chown=1000:1000 alembic.ini ./
COPY --chown=1000:1000 alembic ./alembic
COPY --chown=1000:1000 app ./app
COPY --chown=1000:1000 --from=frontend-builder /app/frontend_dist ./app/frontend_dist
ENV DATA_DIR=/data APP_VERSION=${APP_VERSION} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
VOLUME ["/data"]
EXPOSE 8080
USER 1000:1000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
