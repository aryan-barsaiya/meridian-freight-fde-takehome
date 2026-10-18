PY    ?= python3
VENV  := .venv
BIN   := $(VENV)/bin
EMAIL ?= data/emails/035.eml

.PHONY: setup demo claims test eval eval-offline

setup: $(VENV)/.installed

$(VENV)/.installed: requirements.txt
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -r requirements.txt
	@touch $@

demo: setup            ## one email end to end (override with EMAIL=...)
	$(BIN)/python -m meridian $(EMAIL)

claims: setup          ## all 8 sample claims
	$(BIN)/python -m meridian data/emails/03[5-9].eml data/emails/04[0-2].eml

test: setup            ## unit tests, no API key needed
	$(BIN)/python -m pytest -q

eval: setup            ## all 60 emails with model calls
	$(BIN)/python eval/run_eval.py

eval-offline: setup    ## all 60 emails, lookup + PII only, no API key needed
	$(BIN)/python eval/run_eval.py --offline
