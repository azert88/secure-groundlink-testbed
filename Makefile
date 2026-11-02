# Jury entry points. Run ./setup.sh first (creates .venv). Windows: use the python commands in README.md.
PY ?= .venv/bin/python
OBS ?= data/sample/14932721

.PHONY: setup demo test detect attack results verify

setup:            ## create .venv with the pinned dependencies
	./setup.sh

demo:             ## offline demo on data/sample/ (attack + detect, about 1-2 minutes)
	$(PY) scripts/demo.py

test:             ## unit tests
	$(PY) -m pytest -q

verify:           ## check the signed lock of models, thresholds and detector code
	$(PY) -c "import sys; sys.path.insert(0, '.'); from detect.run import verify_models; ok, m = verify_models(); print(m); sys.exit(0 if ok else 1)"

detect:           ## run the detectors on one raw recording folder: make detect OBS=path/to/folder
	mkdir -p out
	$(PY) detect/run.py --obs-dir $(OBS) --out out/$(notdir $(OBS)).detect.json --md out/$(notdir $(OBS)).detect.md
	cat out/$(notdir $(OBS)).detect.md

attack:           ## standard attack on one folder: make attack OBS=path/to/folder
	$(PY) attacks/run_attack.py standard --obs-dir $(OBS) --out out/$(notdir $(OBS))_standard

results:          ## print the results table (eval/results.md)
	cat eval/results.md
