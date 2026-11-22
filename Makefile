PY = .venv/bin/python

.PHONY: setup schema devset devset-verify verify batch test

setup:            ## create the venv and install dependencies
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

schema:           ## print the JSON Schema Claude is asked to fill
	$(PY) -m battery_passport.schema

devset:           ## extract the 10 dev-set datasheets (reuses existing outputs)
	$(PY) -m battery_passport --devset --verify

devset-verify:    ## free: re-check saved dev-set outputs against the PDFs
	$(PY) -m battery_passport --devset --verify-only

verify:           ## free: re-check every saved output (usage: make verify F=documents/LF280K.pdf)
	$(PY) -m battery_passport $(F) --verify-only

batch:            ## all PDFs in documents/, 5 new API calls at a time
	$(PY) -m battery_passport --all --verify --limit 5
