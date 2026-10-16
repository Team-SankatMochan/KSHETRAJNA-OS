PYTHON ?= python3
.DEFAULT_GOAL := test
SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct)
export SOURCE_DATE_EPOCH

.PHONY: test build smoke-wheel smoke-docker reproducible check-python

check-python:
	$(PYTHON) scripts/release_tools.py check-python

# Offline: neither the runtime nor unittest needs third-party packages.
test: check-python
	PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

# Install requirements-build.lock in a project virtual environment first.
build: check-python
	$(PYTHON) scripts/release_tools.py build-wheel --output dist

smoke-wheel: build
	$(PYTHON) scripts/smoke_runtime.py --wheel-dir dist

smoke-docker:
	docker build --build-arg SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) -t kshetrajna:smoke .
	$(PYTHON) scripts/smoke_docker.py --image kshetrajna:smoke

reproducible: check-python
	$(PYTHON) scripts/check_reproducible.py --kind wheel
