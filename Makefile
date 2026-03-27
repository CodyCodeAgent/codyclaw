.PHONY: install dev lint test check run clean docker

## install: Install production dependencies
install:
	pip install -e .

## dev: Install with development dependencies
dev:
	pip install -e ".[dev]"

## lint: Run ruff linter
lint:
	ruff check codyclaw/

## lint-fix: Auto-fix lint issues
lint-fix:
	ruff check codyclaw/ --fix

## test: Run all tests
test:
	pytest tests/ -v

## check: Run lint + tests (CI equivalent)
check: lint test

## run: Start CodyClaw gateway
run:
	codyclaw

## docker: Build Docker image
docker:
	docker build -t codyclaw .

## docker-up: Start with docker-compose
docker-up:
	docker compose up -d

## docker-down: Stop docker-compose
docker-down:
	docker compose down

## clean: Remove build artifacts
clean:
	rm -rf dist/ build/ *.egg-info/ .pytest_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

## help: Show this help message
help:
	@grep -E '^## ' Makefile | sed 's/## //' | column -t -s ':'
