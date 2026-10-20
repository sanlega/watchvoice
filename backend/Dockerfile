# Build context: repo root. Tests read ../contracts, so keep the repo layout under /repo.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /repo/backend
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY contracts/ /repo/contracts/
COPY backend/ ./

# The image cannot be built if the suite fails (ADR-0001 §13).
FROM base AS test
RUN python -m pytest -q && touch /tests-passed

FROM base AS runtime
COPY --from=test /tests-passed /tests-passed
RUN useradd --system --uid 10001 vox
USER vox
EXPOSE 8080
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
