FROM ghcr.io/berriai/litellm:main-stable

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Gateway config
COPY config.yaml /app/config.yaml

# Admin API + reverse proxy in one small Python service
COPY admin/ /app/admin/

# Install admin deps (flask, gunicorn, requests, pyyaml)
# The base image's venv has pip stripped. Bootstrap pip via ensurepip,
# then install requirements.
RUN python -m ensurepip --upgrade && \
    python -m pip install --no-cache-dir -r /app/admin/requirements.txt

# The base image sets ENTRYPOINT ["litellm"], which prepends `litellm` to
# whatever CMD holds. Clear it so CMD runs as written.
ENTRYPOINT []

# Single port: /admin/* -> Flask admin, everything else -> LiteLLM.
# Bind whatever port Railway injects via $PORT (defaults to 8080).
# gthread worker so one worker serves many concurrent requests.
CMD ["sh", "-c", "exec python -m gunicorn \
     --worker-class=gthread \
     --workers=1 \
     --threads=16 \
     --timeout=600 \
     --graceful-timeout=30 \
     --bind=0.0.0.0:${PORT:-4000} \
     --access-logfile=- \
     admin.router:app"]