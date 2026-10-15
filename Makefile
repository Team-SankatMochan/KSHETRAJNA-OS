PYTHON ?= python3

.PHONY: test build

# Offline: neither the runtime nor unittest needs third-party packages.
test:
	PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

# Install requirements-build.lock in a project virtual environment first.
build:
	$(PYTHON) -m pip wheel --no-build-isolation --no-deps . --wheel-dir dist
