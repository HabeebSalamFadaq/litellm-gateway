FROM ghcr.io/berriai/litellm-database:main-stable

WORKDIR /app

# Gateway config
COPY config.yaml /app/config.yaml

# Admin API + reverse proxy in one small Python service
COPY admin/ /app/admin/

# The base image ships without flask/gunicorn. `pip` is not on PATH in this
# image, so invoke it through the interpreter.
RUN python -m pip install --no-cache-dir -r /app/admin/requirements.txt

# The base image sets ENTRYPOINT ["litellm"], which would prepend `litellm` to
# anything in CMD. Clear it so CMD runs as given.
ENTRYPOINT []

# Single port: 4000 - routes /admin/* to Flask, everything else to LiteLLM
CMD ["python", "-m", "gunicorn", "--workers=1", "--bind=0.0.0.0:4000", "--timeout=300", "admin.router:app"]
