# Shortcuts. On Windows without make, run the commands shown in each target.
COMPOSE      = docker compose
COMPOSE_MOCK = docker compose -f docker-compose.yml -f docker-compose.mock.yml
export GIT_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo local)
export BUILD_DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

.PHONY: help up up-mock down reset agent agent-mock lint test dashboards check-dashboards

help:            ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-17s %s\n", $$1, $$2}'

up:              ## Start the stack (real providers, keys from .env)
	$(COMPOSE) up -d --build

up-mock:         ## Start the stack against the mock provider (no keys)
	$(COMPOSE_MOCK) up -d --build

down:            ## Stop the stack, keep data
	$(COMPOSE_MOCK) down

reset:           ## Stop the stack and delete all data (demo data reloads on next start)
	$(COMPOSE_MOCK) down -v

agent:           ## Run the agent task suite through the gateway
	$(COMPOSE) run --rm agent

agent-mock:      ## Run the agent task suite against the mock provider
	$(COMPOSE_MOCK) run --rm agent

lint:            ## Ruff
	ruff check .

test:            ## Unit tests
	python -m pytest

dashboards:      ## Regenerate Grafana dashboards from tools/build_dashboards.py
	python tools/build_dashboards.py

check-dashboards: ## Run every dashboard query against the running Grafana
	python tools/check_dashboards.py --require-data
