# Index digest verified against registry-1.docker.io (see scripts/toolchain-sources.json).
FROM python:3.13.16-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS build
# Default is a reproducible fallback for a source snapshot; CI/Make use the commit epoch.
ARG SOURCE_DATE_EPOCH=315532800
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements-build.lock ./
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps -r requirements-build.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY packaging ./packaging
RUN PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
RUN python scripts/release_tools.py build-wheel --output /wheels
RUN python scripts/smoke_runtime.py --wheel-dir /wheels

FROM python:3.13.16-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
    && rm -r /wheels \
    && useradd --create-home --uid 10001 app
COPY scripts/smoke_runtime.py scripts/smoke_windows_bundle.py /opt/release-tools/
USER app
WORKDIR /home/app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Deliberately loopback-only. On Linux use --network host, not port publishing.
# Windows telemetry is not available in this synthetic demo container.
CMD ["python", "-m", "kshetrajna", "--demo", "--no-browser", "--port", "8766"]
