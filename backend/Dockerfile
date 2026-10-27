FROM python:3.12-slim
# OpenCASCADE needs a few X/GL shared libraries even when headless.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxrender1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY meshtomod ./meshtomod
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "meshtomod.api:app", "--host", "0.0.0.0", "--port", "8000"]
