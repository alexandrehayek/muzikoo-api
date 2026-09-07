PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help install db-bootstrap db-init db-load db-load-sample db-index train api dev health reset test \
        db-dump db-restore db-push-slim prod-check

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## create .venv and install dependencies (dev = runtime + uvicorn + pytest)
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

db-bootstrap: ## create the role + database (needs PGPASSWORD of a superuser)
	./scripts/bootstrap_db.sh

db-init: ## create the tracks table
	$(PY) scripts/init_db.py --stage schema

db-load: ## stream data/dataset.json into the table (~500k rows)
	$(PY) scripts/load_data.py --truncate

db-load-sample: ## load only the first 5k rows, for a quick end-to-end check
	$(PY) scripts/load_data.py --truncate --limit 5000

db-index: ## build the indexes (run AFTER db-load)
	$(PY) scripts/init_db.py --stage indexes

train: ## fit the KNN model and save models/knn_model.joblib
	$(PY) scripts/train_model.py

api: ## serve the API on http://localhost:8000
	.venv/bin/uvicorn muzikoo.api:app --host 0.0.0.0 --port 8000

dev: ## serve with auto-reload
	.venv/bin/uvicorn muzikoo.api:app --reload --port 8000

health: ## curl the health endpoint
	@curl -s http://localhost:8000/health | $(PY) -m json.tool

# --- production (Supabase / Vercel) ---------------------------------------

db-dump: ## dump the local database to dumps/*.dump
	./scripts/db_dump.sh

db-restore: ## restore a dump into Supabase (SUPABASE_DB_URL=... DUMP=...)
	./scripts/db_restore.sh $(DUMP)

db-push-slim: ## copy to Supabase without lyrics, to fit the 500 MB free tier
	./scripts/db_push_slim.sh

prod-check: ## verify DATABASE_URL points at a usable database
	@DATABASE_URL="$(DATABASE_URL)" $(PY) scripts/check_prod.py

reset: ## drop and recreate the table (destroys all loaded data)
	$(PY) scripts/init_db.py --stage schema --drop

test: ## run the test suite
	$(PY) -m pytest tests/ -q
