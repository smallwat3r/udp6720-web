.DEFAULT_GOAL := help
.PHONY: help install upgrade run mock service service-remove test lint format clean

# Extra flags for run, mock and service, e.g. make run ARGS="--host 0.0.0.0 --safe-start"
ARGS ?=

help: ## List the available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create .venv with the exact versions from uv.lock, plus dev tools
	uv sync --locked --extra dev

upgrade: ## Move uv.lock to the newest allowed versions, then reinstall
	uv lock --upgrade
	uv sync --locked --extra dev

run: ## Start the dashboard on the real PSU (ARGS="..." for extra flags)
	.venv/bin/udp6720-web $(ARGS)

mock: ## Start the dashboard on the simulated PSU, no hardware needed
	.venv/bin/udp6720-web --mock $(ARGS)

service: ## Start the dashboard at boot on http://udp6720.local (asks for sudo)
	./service.sh install $(ARGS)

service-remove: ## Stop the dashboard and remove the boot service
	./service.sh remove

test: ## Run the tests
	.venv/bin/python -m pytest -q

lint: ## Check code style and formatting
	ruff check .
	ruff format --check .

format: ## Reformat the code
	ruff format .

clean: ## Remove caches and build artefacts
	find . -path ./.venv -prune -o \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name "*.egg-info" \) -prune -exec rm -rf {} +
