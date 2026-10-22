FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-zenhei && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock ./
COPY app ./app
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home assistant \
    && mkdir /app/data && chown assistant:assistant /app/data
USER assistant
CMD ["python", "-m", "app", "worker"]
