.PHONY: test test-all

VENV := .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

# create the local virtualenv and install base test deps the first time either target runs
$(VENV)/bin/python3:
	python3 -m venv $(VENV)
	$(PIP) install -q pytest dash pandas plotly python-dotenv

# run all offline tests — safe any time, no API key needed
test: $(VENV)/bin/python3
	$(PYTHON) -m pytest -m "not live"

# install provider SDKs and run all offline tests — never touches the real API
test-all: $(VENV)/bin/python3
	$(PIP) install --upgrade pip -q
	$(PIP) install -q openai anthropic  # provider SDKs needed so import-time checks don't fail
	$(PYTHON) -m pytest -m "not live"
