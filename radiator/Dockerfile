# The Information Radiator. Build from the repo root, since it needs the
# shared radiator_schema package:
#   docker build -f radiator/Dockerfile -t testharness-radiator .
FROM ghcr.io/translatorsri/renci-python-image:3.11.5

LABEL org.opencontainers.image.source /github.com/TranslatorSRI/TestHarness

WORKDIR /app

COPY radiator/requirements.txt radiator/requirements.txt
RUN pip install --no-cache-dir -r radiator/requirements.txt

COPY radiator_schema radiator_schema
COPY radiator radiator
RUN rm -rf radiator/tests

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# the non-root user defined in the base image
USER nru

EXPOSE 8000

# --proxy-headers so the login throttle sees each client, not the ingress.
# Migrations run separately (an init container in deploy/radiator.example.yaml):
#   alembic -c radiator/alembic.ini upgrade head
CMD ["uvicorn", "radiator.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
