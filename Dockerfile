FROM ghcr.io/berriai/litellm:main-stable

# Gateway config
COPY config.yaml /app/config.yaml

# Admin API + reverse proxy in one small Python service
COPY admin/ /app/admin/
RUN pip install --no-cache-dir -r /app/admin/requirements.txt

# Single port: 4000 - routes /admin/* to Flask, everything else to LiteLLM
CMD ["sh", "-c", "exec gunicorn -w 1 -b 0.0.0.0:4000 admin.router:app"]
