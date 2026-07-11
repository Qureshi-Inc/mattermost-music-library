.PHONY: install dev lint format typecheck test test-cov docker-build docker-up docker-down clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

lint:
	ruff check .

format:
	ruff format .
	ruff check --fix .

typecheck:
	mypy app/

test:
	pytest

test-cov:
	pytest --cov=app --cov-report=term-missing --cov-report=html

docker-build:
	docker build -t slaptastic .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.py[cod]" -delete 2>/dev/null || true
