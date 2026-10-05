FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home app
USER app

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn plot_check.api:app_from_env --factory --host 0.0.0.0 --port ${PORT}"]
