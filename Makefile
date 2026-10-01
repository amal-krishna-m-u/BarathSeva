# BarathSeva AI — common tasks.
# `make setup` once, then `make dev` in one shell.

PY := backend/.venv/bin/python
PIP := backend/.venv/bin/pip

.DEFAULT_GOAL := help
.PHONY: help setup services db seed api web worker beat test demo lint clean reset

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv, install backend + frontend deps
	python3.11 -m venv backend/.venv || python3 -m venv backend/.venv
	$(PIP) install --quiet --upgrade pip
	$(PIP) install -r backend/requirements.txt -r backend/requirements-dev.txt
	cd frontend && npm install

services: ## Start PostGIS + Redis containers
	docker compose up -d
	@echo "waiting for health..."
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' barathseva-db)" = "healthy" ]; do sleep 2; done
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' barathseva-redis)" = "healthy" ]; do sleep 2; done
	@echo "postgis + redis healthy"

db: services ## Create the schema and seed reference data
	cd backend && .venv/bin/python scripts/init_db.py

reset: services ## DESTRUCTIVE: drop and rebuild the database
	docker exec barathseva-db psql -U barathseva -d barathseva \
		-c "DROP SEQUENCE IF EXISTS complaint_reference_seq;" >/dev/null
	cd backend && .venv/bin/python scripts/init_db.py --drop

api: ## Run the FastAPI backend on :8000
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

web: ## Run the Next.js frontend on :3000
	cd frontend && npm run dev

worker: ## Run the Celery worker (SLA sweeps)
	cd backend && .venv/bin/celery -A app.worker.celery_app worker --loglevel=info

beat: ## Run the Celery beat scheduler
	cd backend && .venv/bin/celery -A app.worker.celery_app beat --loglevel=info

test: ## Run the backend test suite
	cd backend && .venv/bin/python -m pytest

demo: ## Rebuild the DB and run the full lifecycle demonstration
	cd backend && .venv/bin/python scripts/demo_lifecycle.py --reset

lint: ## Typecheck the frontend
	cd frontend && npx tsc --noEmit && npm run lint

clean: ## Stop containers and remove caches
	docker compose down
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache frontend/.next
