.PHONY: install test lint demo campus-demo verify-campus serve eval
install:
	uv sync --extra dev --extra mcp --frozen
lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
test:
	.venv/bin/pytest -q
demo:
	.venv/bin/researchos
campus-demo:
	.venv/bin/researchos --corpus campus --campus 威海 --document-year 2024 --output artifacts/campus
serve:
	.venv/bin/uvicorn researchos.api.app:app --host 127.0.0.1 --port 8000
eval:
	.venv/bin/python scripts/evaluate.py

privacy:
	.venv/bin/python scripts/check_public.py

verify-campus:
	.venv/bin/python scripts/verify_campus_sources.py
