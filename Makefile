.PHONY: install install-dev lint test layer deploy clean

# Install production dependencies
install:
	pip install -e .

# Install with dev dependencies
install-dev:
	pip install -e ".[dev,cdk]"

# Run linters
lint:
	ruff check src tests
	ruff format --check src tests
	mypy src

# Format code
format:
	ruff format src tests
	ruff check --fix src tests

# Run tests
test:
	pytest tests/unit -v

# Build Lambda layer (Linux ARM64 for Lambda)
layer:
	rm -rf layer
	mkdir -p layer/python
	pip install \
		--platform manylinux2014_aarch64 \
		--target layer/python \
		--only-binary=:all: \
		--implementation cp \
		--python-version 3.12 \
		pydantic httpx anthropic aws-lambda-powertools
	@echo "Layer built at ./layer (manylinux2014_aarch64)"

# Deploy to AWS
deploy: layer
	cd cdk && cdk deploy --require-approval never

# Synth CDK (dry run)
synth:
	cd cdk && cdk synth

# Clean build artifacts
clean:
	rm -rf layer/
	rm -rf cdk.out/
	rm -rf __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true


