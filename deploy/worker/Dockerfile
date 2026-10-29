FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY server/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt && useradd --uid 10001 --create-home worker
COPY --chown=worker:worker server/app ./app
COPY --chown=worker:worker server/scripts ./scripts
USER worker
ENTRYPOINT ["python", "-m", "scripts.run_worker"]
