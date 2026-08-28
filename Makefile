###############################################################################
# Local development helpers
###############################################################################

# Start the gateway locally on port 4000 using your .env file.
# Requires: pip install 'litellm[proxy]'
local:
	litellm --config config.yaml --port 4000 --host 0.0.0.0 --detailed_debug

# Build the Docker image locally (does not need a registry)
build:
	docker build -t litellm-gateway:local .

# Run the local Docker image
run:
	docker run --rm -p 4000:4000 --env-file .env litellm-gateway:local

# Test the gateway after starting it locally
test:
	python test_gateway.py

# Validate config.yaml syntax without starting the proxy
validate:
	litellm --config config.yaml --help >/dev/null
