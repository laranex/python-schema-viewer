# Container isolation tests

`tests/test_isolation.py` runs its `ContainerIsolation` tests only when `LSV_TEST_CONTAINER_IMAGE` names an image
that has this package, at the same version, and SQLAlchemy installed:

```bash
python -m build --wheel --outdir /tmp/lsv-image
cp tests/containers/Dockerfile /tmp/lsv-image/
docker build -t lsv-test /tmp/lsv-image
LSV_TEST_CONTAINER_IMAGE=lsv-test python -m unittest tests.test_isolation -v
```

On macOS and Windows, Docker Desktop must share the project directory and the system temporary directory.
