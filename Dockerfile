FROM python:3.14-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
COPY campaign_optimizer ./campaign_optimizer
COPY .ontology_bundles ./.ontology_bundles
COPY tests/fixtures ./tests/fixtures
COPY app.py ./

RUN uv sync --frozen --no-dev --no-install-project

EXPOSE 8501

CMD ["uv", "run", "--no-sync", "python", "-m", "streamlit", "run", "app.py", "--server.headless", "true"]
