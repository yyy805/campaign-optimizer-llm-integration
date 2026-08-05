FROM python:3.12.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/service

RUN groupadd --system app && useradd --system --gid app --home /service app
WORKDIR /service

COPY ["ontology review api/pyproject.toml", "./"]
COPY ["ontology review api/app", "./app"]
COPY ["ontology review api/alembic.ini", "./"]
COPY ["ontology review api/migrations", "./migrations"]
COPY ["campaign_optimizer", "./campaign_optimizer"]
RUN python -m pip install --upgrade "pip==25.2" && python -m pip install .

COPY ["docs/ontology/ontology 概念卡", "/ontology"]
COPY ["campaign_optimizer/schemas/final_plan.schema.json", "/contracts/final_plan.schema.json"]
COPY ["campaign_optimizer/schemas/ontology_review.schema.json", "/contracts/ontology_review.schema.json"]
RUN mkdir -p /data && chown -R app:app /service /data

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
