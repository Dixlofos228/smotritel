FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata && rm -rf /var/lib/apt/lists/* && useradd -u 1000 -m bot && mkdir /data && chown bot:bot /data
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY smotritel ./smotritel
COPY migrations ./migrations
COPY scripts ./scripts
COPY assets ./assets
USER bot
CMD ["python", "-m", "smotritel"]
