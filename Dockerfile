FROM ghcr.io/berriai/litellm-database:main-stable

WORKDIR /app

# Gateway config
COPY config.yaml /app/config.yaml

# Admin API + reverse proxy in one small Python service
COPY admin/ /app/admin/

# The base image ships a uv-built venv at /app/.venv with pip stripped out
# ("No module named pip"), so neither `pip` nor `python -m pip` works here.
# Bring in uv as a static binary and install straight into that venv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN uv pip install --python /app/.venv/bin/python --no-cache \
    -r /app/admin/requirements.txt

# The base image sets ENTRYPOINT ["litellm"], which prepends `litellm` to
# whatever CMD holds. Clear it so CMD runs as written.
ENTRYPOINT []

# Single port: /admin/* -> Flask admin, everything else -> LiteLLM.
# Bind whatever port Railway injects via $PORT (it defaults to 8080 and does
# not have to be 4000); hardcoding 4000 leaves nothing on the routed port.
# gthread (not the default sync worker) so one worker serves many concurrent
# requests; a sync worker would serialise the whole gateway. Worker count
# stays at 1 because the worker owns the LiteLLM subprocess.
CMD ["sh", "-c", "exec /app/.venv/bin/python -m gunicorn \
     --worker-class=gthread \
     --workers=1 \
     --threads=16 \
     --timeout=600 \
     --graceful-timeout=30 \
     --bind=0.0.0.0:${PORT:-4000} \
     --access-logfile=- \
     admin.router:app"]
