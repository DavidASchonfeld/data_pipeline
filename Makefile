.PHONY: test test-all test-agent-data lint-layers venv-reset

VENV := .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip
# marker file: its timestamp records when agent_data (+ its dev tools) was last installed into .venv
AGENT_DATA_STAMP := $(VENV)/.agent-data-installed

# create the local virtualenv and install base test deps the first time either target runs
# sqlglot is here (not just in test-all) because the EPIC 9 SQL safety layer's offline tests parse SQL
$(VENV)/bin/python3:
	python3 -m venv $(VENV)
	$(PIP) install -q pytest dash pandas plotly python-dotenv beautifulsoup4 sqlglot

# install agent_data in editable mode with its dev extra; re-runs automatically whenever pyproject.toml changes
$(AGENT_DATA_STAMP): pyproject.toml | $(VENV)/bin/python3
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"
	touch $@

# run all offline tests — safe any time, no API key needed
test: $(VENV)/bin/python3 $(AGENT_DATA_STAMP)
	$(PYTHON) -m pytest -m "not live"

# install provider SDKs and run all offline tests — never touches the real API
test-all: $(VENV)/bin/python3 $(AGENT_DATA_STAMP)
	$(PIP) install --upgrade pip -q
	$(PIP) install -q openai anthropic  # provider SDKs needed so import-time checks don't fail
	$(PYTHON) -m pytest -m "not live"

# fast loop while working on agent_data only: lint it, then run just its offline tests
test-agent-data: $(AGENT_DATA_STAMP)
	$(VENV)/bin/ruff check agent_data
	$(PYTHON) -m pytest agent_data -m "not live"

# check the layer rules in pyproject.toml (engine never imports domains/config/platform; no genai or frameworks)
lint-layers: $(AGENT_DATA_STAMP)
	$(VENV)/bin/lint-imports

# clean slate: delete .venv and rebuild it from scratch — only needed if the environment looks broken
venv-reset:
	rm -rf $(VENV)
	$(MAKE) $(AGENT_DATA_STAMP)
