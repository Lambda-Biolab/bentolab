.PHONY: help setup validate lint_fix quick_validate check_complexity check_links check_docs test scan-ble scan-wifi commander monitor-ble clean

VENV := .venv/bin
PYTHON := $(VENV)/python
SRC := bentolab/ tests/ tools/

# ---- Core targets ----

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install all dependencies
	uv venv --python 3.13
	uv pip install -e ".[dev,tools]"
	@echo "Activate with: source .venv/bin/activate"

# ---- Validation targets ----

validate: ## Full read-only validation (format, lint, types, complexity, tests)
	$(VENV)/ruff format --check $(SRC)
	$(VENV)/ruff check $(SRC)
	$(VENV)/pyright bentolab/
	$(VENV)/complexipy bentolab/
	$(VENV)/pytest tests/ -v -m "not hardware"

lint_fix: ## Auto-fix lint and format issues
	$(VENV)/ruff format $(SRC)
	$(VENV)/ruff check --fix $(SRC)

quick_validate: ## Quick check: ruff + pyright (skip tests)
	$(VENV)/ruff format --check $(SRC)
	$(VENV)/ruff check $(SRC)
	$(VENV)/pyright bentolab/

check_complexity: ## Run complexipy analysis
	$(VENV)/complexipy bentolab/

check_links: ## Check links with lychee
	lychee --config .lychee.toml .

check_docs: ## Lint markdown files
	markdownlint-cli2 "**/*.md" "#node_modules"

test: ## Run test suite (excludes hardware tests)
	$(VENV)/pytest tests/ -v -m "not hardware"

# ---- Tool runners ----

scan-ble: ## Scan for BLE devices (10s)
	$(PYTHON) tools/ble_scanner.py --scan-time 10

scan-wifi: ## Scan local network for Bento Lab Wi-Fi unit
	$(PYTHON) tools/wifi_scanner.py

commander: ## Launch interactive BLE commander
	$(PYTHON) tools/ble_commander.py

monitor-ble: ## Monitor BLE notifications (Ctrl+C to stop)
	$(PYTHON) tools/ble_monitor.py

# ---- Cleanup ----

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info/
