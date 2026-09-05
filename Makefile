# EchoNeura — developer tasks.
# `make help` lists everything. Nothing here assumes a GPU, Docker or root.

SHELL := /bin/bash
BACKEND_DIR := backend
FRONTEND_DIR := frontend
VENV := $(BACKEND_DIR)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff

.DEFAULT_GOAL := help

## help: list available targets
.PHONY: help
help:
	@echo "EchoNeura"
	@echo
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  make /' | awk -F': ' '{printf "%-22s %s\n", $$1, $$2}'
	@echo

## setup: create the Python venv, install backend + frontend dependencies
.PHONY: setup
setup: venv frontend-deps env
	@echo "✓ setup complete — run 'make dev'"

$(VENV):
	cd $(BACKEND_DIR) && python3 -m venv .venv

## venv: install/refresh backend Python dependencies
.PHONY: venv
venv: $(VENV)
	$(PIP) install -q --disable-pip-version-check -U pip
	$(PIP) install -q --disable-pip-version-check -r $(BACKEND_DIR)/requirements.txt ruff
	@echo "✓ backend dependencies installed"

## frontend-deps: install frontend npm dependencies
.PHONY: frontend-deps
frontend-deps:
	cd $(FRONTEND_DIR) && npm install --no-audit --no-fund
	@echo "✓ frontend dependencies installed"

## env: create .env from .env.example if missing
.PHONY: env
env:
	@test -f .env || cp .env.example .env
	@echo "✓ .env ready"

## dev: run the API (with in-process worker) and the web UI together
.PHONY: dev
dev: env
	@bash scripts/dev.sh

## dev-backend: API + in-process queue worker on :8000 (Swagger at /api/docs)
.PHONY: dev-backend
dev-backend: venv env
	cd $(BACKEND_DIR) && PYTHONPATH=. ../$(UVICORN) app.main:app --host 0.0.0.0 --port $${PORT:-8000} --reload

## dev-worker: standalone queue worker (use with ECHONEURA_WORKER_IN_PROCESS=false)
.PHONY: dev-worker
dev-worker: venv env
	cd $(BACKEND_DIR) && PYTHONPATH=. ../$(PY) -m app.worker

## dev-frontend: Next.js dev server on :3000
.PHONY: dev-frontend
dev-frontend: frontend-deps
	cd $(FRONTEND_DIR) && npm run dev

## test: run the backend test suite
.PHONY: test
test: venv
	cd $(BACKEND_DIR) && PYTHONPATH=. ../$(PYTEST)

## test-fast: run the suite without the end-to-end/HTTP tests
.PHONY: test-fast
test-fast: venv
	cd $(BACKEND_DIR) && PYTHONPATH=. ../$(PYTEST) -m "not slow"

## lint: ruff check + format check + tsc
.PHONY: lint
lint: venv
	cd $(BACKEND_DIR) && ../$(RUFF) check app tests
	cd $(BACKEND_DIR) && ../$(RUFF) format --check app tests
	cd $(FRONTEND_DIR) && npx tsc --noEmit
	@echo "✓ lint clean"

## format: auto-format Python and check TS types
.PHONY: format
format: venv
	cd $(BACKEND_DIR) && ../$(RUFF) check --fix app tests
	cd $(BACKEND_DIR) && ../$(RUFF) format app tests
	cd $(FRONTEND_DIR) && npx tsc --noEmit

## typecheck: frontend TypeScript only
.PHONY: typecheck
typecheck:
	cd $(FRONTEND_DIR) && npx tsc --noEmit

## audit: report known-vulnerable dependencies
.PHONY: audit
audit:
	cd $(FRONTEND_DIR) && npm audit
	$(PIP) list --outdated 2>/dev/null | head -20 || true

## build: production build of the frontend
.PHONY: build
build:
	cd $(FRONTEND_DIR) && npm run build

## sample: generate a demo WAV you can upload (data/samples/demo.wav)
.PHONY: sample
sample: venv
	cd $(BACKEND_DIR) && PYTHONPATH=. ../$(PY) ../tools/make_sample_audio.py
	@echo "✓ data/samples/demo.wav — upload it at http://localhost:3000"

## reset-db: delete the local SQLite database (uploads are kept)
.PHONY: reset-db
reset-db:
	@rm -f data/echoneura.db data/echoneura.db-wal data/echoneura.db-shm
	@echo "✓ database removed; it is recreated on next start"

## clean: remove build artifacts, caches and the venv
.PHONY: clean
clean:
	rm -rf $(VENV) $(FRONTEND_DIR)/.next $(FRONTEND_DIR)/node_modules
	find . -path ./node_modules -prune -o -name '__pycache__' -type d -print0 2>/dev/null | xargs -0 rm -rf
	rm -rf $(BACKEND_DIR)/.pytest_cache $(BACKEND_DIR)/.ruff_cache
	@echo "✓ cleaned"
