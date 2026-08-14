SHELL := /bin/bash

DEFAULT_MODE := git
MODE ?= $(DEFAULT_MODE)

.DEFAULT_GOAL := help


# Written to .secrets_mode file in module-ttt during DevPod setup:
#   infisical → use Infisical for secrets
#   dotenv    → use a plain .env file
#
# Defaults to infisical when the file is missing.
SECRETS_MODE := $(shell cat .secrets_mode 2>/dev/null || echo infisical)


local:
ifeq ($(SECRETS_MODE),dotenv)
	set -a && source .env && set +a && \
	poetry run python -m spacy download en_core_web_md && \
	poetry run python -m debugpy --listen 0.0.0.0:5690 \
		-m uvicorn app.main:app \
		--host 0.0.0.0 \
		--port 8090 \
		--loop uvloop \
		--http httptools \
		--reload \
		--reload-dir ./ \
		--reload-dir ../base-tdb-models \
		--reload-dir ../base-tdb-clients \
		--reload-dir ../base-tdb-helpers \
		--reload-dir ../package-content-elementizer
else
	infisical run --watch -- sh -c '\
		poetry run python -m spacy download en_core_web_md && \
		poetry run python -m debugpy --listen 0.0.0.0:5690 \
			-m uvicorn app.main:app \
			--host 0.0.0.0 \
			--port 8090 \
			--loop uvloop \
			--http httptools \
			--reload \
			--reload-dir ./ \
			--reload-dir ../base-tdb-models \
			--reload-dir ../base-tdb-clients \
			--reload-dir ../base-tdb-helpers \
			--reload-dir ../package-content-elementizer'
endif


run:
ifeq ($(SECRETS_MODE),dotenv)
	set -a && source .env && set +a && \
	poetry run python -m spacy download en_core_web_md && \
	poetry run python -m uvicorn app.main:app \
		--host 0.0.0.0 \
		--port 8090 \
		--workers 4 \
		--loop uvloop \
		--http httptools
else
	infisical run -- sh -c '\
		poetry run python -m spacy download en_core_web_md && \
		poetry run python -m uvicorn app.main:app \
			--host 0.0.0.0 \
			--port 8090 \
			--workers 4 \
			--loop uvloop \
			--http httptools'
endif


test:
	@echo "🧪 Running tests with coverage..."
	poetry run pytest \
		--cov=app \
		--cov-report=term-missing \
		--cov-report=xml


install-dev:
	@echo "📦 Installing development dependencies..."
	poetry install --with dev --no-root --no-interaction --no-ansi
	@echo "✅ Development dependencies installed!"


sync:
	@echo "🔄 Running sync_git_deps.py with mode: $(MODE)"
	python3 sync_git_deps.py --mode "$(MODE)"


sync-dry-run:
	@echo "🔍 Dry-run sync for validation (mode: $(MODE))"
	python3 sync_git_deps.py --mode "$(MODE)" --dry-run


install-hooks:
	@echo "Installing git hooks..."
	@cp -f git-hooks/* .git/hooks/
	@chmod +x .git/hooks/* 2>/dev/null || true
	@echo "Git hooks installed!"


help:
	@echo ""
	@echo "Targets:"
	@echo "  make local                         → start local stack"
	@echo "  make run                           → start production server"
	@echo "  make test                          → run tests with coverage"
	@echo "  make install-dev                   → install development dependencies"
	@echo "  make sync MODE=<git|local>         → sync git deps (default: git)"
	@echo "  make sync-dry-run MODE=<git|local> → validate deps without changing files"
	@echo "  make install-hooks                 → install git hooks"
	@echo ""