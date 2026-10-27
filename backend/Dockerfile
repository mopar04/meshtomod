FROM python:3.12-slim
# OpenCASCADE needs a few X/GL shared libraries even when headless.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY nurbsmith ./nurbsmith
RUN pip install --no-cache-dir .
# Run as an unprivileged user; results go to /data (a volume in production).
RUN useradd --system --uid 10001 --home-dir /app meshtomod \
    && mkdir /data && chown meshtomod /data
USER meshtomod
ENV MESHTOMOD_DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"
# One server process: the job queue lives in it. Conversions run in their own processes.
CMD ["uvicorn", "nurbsmith.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
