.PHONY: setup lint format test scan-ble scan-wifi commander monitor-ble clean help

VENV := .venv/bin
PYTHON := $(VENV)/python

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install all dependencies
	uv venv --python 3.13
	uv pip install -r requirements.txt
	uv pip install ruff
	@echo "Activate with: source .venv/bin/activate"

lint: ## Run ruff linter
	$(VENV)/ruff check tools/ bentolab/ tests/

format: ## Format code with ruff
	$(VENV)/ruff format tools/ bentolab/ tests/
	$(VENV)/ruff check --fix tools/ bentolab/ tests/

test: ## Run test suite
	$(VENV)/pytest tests/ -v

scan-ble: ## Scan for BLE devices (10s)
	$(PYTHON) tools/ble_scanner.py --scan-time 10

scan-wifi: ## Scan local network for Bento Lab Wi-Fi unit
	$(PYTHON) tools/wifi_scanner.py

commander: ## Launch interactive BLE commander
	$(PYTHON) tools/ble_commander.py

monitor-ble: ## Monitor BLE notifications (Ctrl+C to stop)
	$(PYTHON) tools/ble_monitor.py

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info/
