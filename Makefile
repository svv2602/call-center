.PHONY: install test test-integration test-all lint typecheck format check clean

install:
	python3.12 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev,test]"

test:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v -m integration

test-all:
	pytest tests/ --cov=src --cov-report=html

lint:
	ruff check src/
	ruff format --check src/

typecheck:
	mypy src/ --strict

format:
	ruff format src/
	ruff check src/ --fix

check: lint typecheck test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .mypy_cache .ruff_cache *.egg-info build dist
