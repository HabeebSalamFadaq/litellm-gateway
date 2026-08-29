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

# Single port 4000: /admin/* -> Flask admin, everything else -> LiteLLM.
# gthread (not the default sync worker) so one worker can serve many
# concurrent requests; a sync worker would serialise the whole gateway.
# Only one worker, because the worker also owns the LiteLLM subprocess.
CMD ["/app/.venv/bin/python", "-m", "gunicorn", \
     "--worker-class=gthread", \
     "--workers=1", \
     "--threads=16", \
     "--timeout=600", \
     "--graceful-timeout=30", \
     "--bind=0.0.0.0:4000", \
     "--access-logfile=-", \
     "admin.router:app"]
