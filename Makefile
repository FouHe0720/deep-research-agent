.PHONY: install test lint demo serve eval
install:
	uv sync --extra dev --extra mcp --frozen
lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
test:
	.venv/bin/pytest -q
demo:
	.venv/bin/researchos
serve:
	.venv/bin/uvicorn researchos.api.app:app --host 127.0.0.1 --port 8000
eval:
	.venv/bin/python scripts/evaluate.py

privacy:
	.venv/bin/python scripts/check_public.py
