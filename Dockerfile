FROM python:3.13.16-slim-bookworm AS build
WORKDIR /build
COPY requirements-build.lock ./
RUN python -m pip install --no-cache-dir -r requirements-build.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
RUN PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
RUN python -m pip wheel --no-cache-dir --no-build-isolation --no-deps . --wheel-dir /wheels

FROM python:3.13.16-slim-bookworm
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --no-deps /wheels/*.whl \
    && rm -r /wheels \
    && useradd --create-home --uid 10001 app
USER app
WORKDIR /home/app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Deliberately loopback-only. On Linux use --network host, not port publishing.
# Windows telemetry is not available in this synthetic demo container.
CMD ["python", "-m", "kshetrajna", "--demo", "--no-browser", "--port", "8766"]
