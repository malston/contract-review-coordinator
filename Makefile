# Convenience wrapper around Poetry so you don't have to know Poetry.
# Run `make` (or `make help`) to see the available commands.
#
# Poetry manages this project's dependencies and virtualenv. If it isn't
# installed, the targets below print install instructions instead of failing
# cryptically. See https://python-poetry.org/docs/#installation

.DEFAULT_GOAL := help

.PHONY: help install install-live test lint lint-fix check demo lock clean

help: ## Show this help
	@echo "Contract-Review Coordinator -- common tasks:"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "First time? Run 'make install' then 'make test'."

.PHONY: check-poetry
check-poetry:
	@command -v poetry >/dev/null 2>&1 || { \
		echo "Poetry is not installed -- it manages this project's dependencies."; \
		echo "Install it (any one of):"; \
		echo "  pipx install poetry"; \
		echo "  curl -sSL https://install.python-poetry.org | python3 -"; \
		echo "See https://python-poetry.org/docs/#installation"; \
		exit 1; }

install: check-poetry ## Install the project + dev tools (no API key needed)
	poetry install --with dev

install-live: check-poetry ## Install dev tools + the optional live deps (anthropic, claude-agent-sdk)
	poetry install --with dev --with live

test: check-poetry ## Run the full test suite (no API key needed)
	poetry run pytest

lint: check-poetry ## Check formatting and style with ruff
	poetry run ruff check src tests

lint-fix: check-poetry ## Auto-fix what ruff can
	poetry run ruff check --fix src tests

check: lint test ## Lint, then run the tests

demo: check-poetry ## Run the offline two-trajectory demo
	poetry run python -m contract_review.demo

lock: check-poetry ## Regenerate poetry.lock after changing dependencies
	poetry lock

clean: ## Remove Python and tool caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
