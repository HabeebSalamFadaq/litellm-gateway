###############################################################################
# LiteLLM gateway for Claude Code
# ---------------------------------------------------------------------------
# Uses the official LiteLLM stable image, mounts the config from the repo
# and binds the production port. The image is signed and version-pinned.
# ---------------------------------------------------------------------------
# https://docs.litellm.ai/docs/proxy/docker_quick_start
###############################################################################

FROM ghcr.io/berriai/litellm:main-stable

# The config file is mounted by Railway/Render/etc. We default to a
# baked-in copy so the image also works with `docker run` and local
# testing.
COPY config.yaml /app/config.yaml

# The image already exposes 4000, but we make it explicit for Railway
EXPOSE 4000

# Health checks
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:4000/health/liveliness || exit 1

# Use the LiteLLM stable image's default entrypoint and pass our config.
# --port 4000 is the default but we set it explicitly for Railway.
ENTRYPOINT ["litellm"]
CMD ["--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
