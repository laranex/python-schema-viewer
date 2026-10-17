# Test image for the container isolation tests: this package (from a locally built wheel) and SQLAlchemy, which the
# adversarial fixtures use. No application code and no credentials.
FROM python:3.13-slim
COPY *.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl "SQLAlchemy>=2.0,<3" && rm /tmp/*.whl
