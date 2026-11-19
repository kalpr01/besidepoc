# One image, two processes (run as separate containers in the same Container App):
#   ui     -> uvicorn app:app            --host 127.0.0.1 --port 8000   (default CMD)
#   bridge -> uvicorn lithic_bridge:app  --host 127.0.0.1 --port 8001   (command override)
# Both bind to loopback only; the Caddy proxy container is the only thing on the ingress port.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./
COPY static ./static
COPY db ./db

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin occlet \
    && chown -R occlet:occlet /app
USER 10001

EXPOSE 8000 8001
CMD ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"]
