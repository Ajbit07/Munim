# Munim in one container: the API, the three agents, the ops console and the merchant chat.
# Nothing here reaches the network at run time; the dataset is generated inside the container.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MFP_SEED=42 \
    MFP_PORT=8000

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install setuptools>=68 && pip install -e ".[dev]"

COPY config ./config
COPY ui ./ui
COPY tools ./tools
COPY tests ./tests
COPY workflow ./workflow
COPY serve.py generate.py demo.py evaluate.py rehearse.py ./
COPY docker/entrypoint.sh /usr/local/bin/munim

RUN sed -i 's/\r$//' /usr/local/bin/munim && chmod +x /usr/local/bin/munim \
    && mkdir -p data/generated data/uploads reports samples \
    && useradd --create-home --uid 10001 munim \
    && chown -R munim:munim /app
USER munim

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/portfolio', timeout=3)"

ENTRYPOINT ["munim"]
CMD ["serve"]
