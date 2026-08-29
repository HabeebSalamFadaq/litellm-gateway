# Use the litellm-database image which has admin UI dependencies pre-installed
FROM ghcr.io/berriai/litellm-database:main-stable

# Gateway config
COPY config.yaml /app/config.yaml

# Admin API + reverse proxy in one small Python service
COPY admin/ /app/admin/

# Single port: 4000 - routes /admin/* to Flask, everything else to LiteLLM
# Use exec form and let gunicorn handle workers via env var
CMD ["gunicorn", "--workers=1", "-b", "0.0.0.0:4000", "admin.router:app"]
