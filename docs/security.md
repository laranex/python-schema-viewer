# Build security

`build` has to import your models, and importing a module runs its code. Python Schema Viewer runs that code in a separate **extraction worker** and limits what reaches it. This page lists what is guaranteed and what is only best-effort. Keep the two apart when deciding where to run a build.

`serve` and `mcp` read a saved document only. They never import application code or connect to a database, at any isolation level. [Database introspection](./database.md) runs in a second worker with different powers, described [below](#database-introspection).

## Threat model

The code being imported is your application: models, settings modules, and everything they import, including mapper callbacks and Django `AppConfig.ready()`. Usually it is trusted but careless: an import that opens a database, creates tables, writes a log file, calls a service, or reads `DATABASE_URL`. Sometimes it is a dependency or a pull request you have not reviewed.

The build protects:

- databases, which must not receive connections, DDL, DML, or queries from the extraction step;
- database credentials, which must not reach application code;
- the project and the machine, which should not change because a schema was extracted;
- the network, which should not be reached during extraction.

Database introspection adds a database URL. That URL has to reach the catalogs and nothing else: not application code, not logs or error messages, and not a write to the database.

## Isolation levels

Choose a level with `--isolation` or `isolation` in `[tool.laranex-schema-viewer]`. `process` is the default and suits trusted code on a developer machine. Use `container` in CI, for code you have not reviewed, or anywhere a stray side effect would matter. Both levels run the worker as a separate program with a timeout. When a level cannot be set up (no container runtime, no image, or the wrong package version in the image), the build stops with an error. It never falls back to a weaker level, and there is no in-process level.

| | `process` (default) | `container` |
| --- | --- | --- |
| Separate process, timeout, cleanup on exit | Yes | Yes (disposable container) |
| Environment | Allowlist only | Allowlist only |
| Network | Best-effort guard | None (`--network none`) |
| Project files | Writable by the user; best-effort guard | Read-only mount |
| Rest of the filesystem | Readable and writable by the user; best-effort guard | Image's read-only root, a 128 MB `/tmp` |
| Privileges | Your user | Image user mapped to your uid, all capabilities dropped, no new privileges, 256 processes |

## Guaranteed protections

These are enforced by the operating system or the container runtime. Application code cannot undo them from inside the worker.

**Both levels**

- **Credentials in the environment are withheld.** The worker starts with an allowlist (`PATH`, locale, `TZ`, `TERM`, Python's own variables, the active virtual environment, `DJANGO_SETTINGS_MODULE`, and the Windows system variables). Every other variable is left out unless you name it in `env_passthrough`. Names that look like credentials (`*PASSWORD*`, `*SECRET*`, `*TOKEN*`, `*_KEY`, `DATABASE_URL`, `*_DSN`, `*_URL`, `*_URI`) are refused there. Put placeholders for settings that need them in `[tool.laranex-schema-viewer.env]`. The build prints how many variables it withheld, never their names or values.
- **Database URLs never reach application code.** The extraction worker has no database configuration of its own. The URL for introspection is read by the parent from its environment variable, which the extraction worker's allowlist excludes. Naming that variable in `env_passthrough` or `env` stops the build.
- **The worker's results are checked.** The worker returns a laranex-schema 2.0 document through a file of at most 32 MiB, and the parent validates it strictly before using it or writing anything. Output on stdout cannot be mistaken for a result. A failed or invalid build leaves the previous document in place.
- **Time is bounded.** A worker that does not finish within `timeout` seconds (default 120) is stopped.

**`process`**

- **The worker and its process group are killed** when it finishes, fails, or times out. Processes left behind by application code are killed with it, including ones started in ways the guards cannot see. A process that leaves the group on purpose (`setsid`, a double fork into a new session) is out of reach. Use `container` for code that might do that.

**`container`**

- **No network:** the container has only a loopback interface.
- **The project is mounted read-only**, and so is the image's root filesystem. Only a size-limited `/tmp` and a disposable output directory can be written. Directories listed in `allow_writes` are still read-only at this level; application code that has to write to them fails.
- **Nothing outlives the build:** the container is removed, or killed on timeout, along with every process in it.
- **Reduced privileges:** all Linux capabilities are dropped, privilege escalation is disabled, and the process count is limited.

The container tests in `tests/test_isolation.py` check these protections. They reach the network and the filesystem through libc, where the in-process guards cannot see them, so a passing test shows the container itself enforcing the limit. See [validation](#validation) for where they have run.

## Best-effort safeguards

The worker installs Python audit hooks and patches the ORMs' connect methods before it imports anything. These guards catch most accidental side effects and report them. They run in the same interpreter as the code they watch, so they are **not a security boundary**: native extensions, `ctypes`, and code that restores the patched methods get past them.

- **Network:** socket connects, binds, sends and host name lookups are refused.
- **Processes:** `subprocess`, `os.system`, `exec*`, `spawn*`, `fork`, and `pty.spawn` are refused.
- **Files:** opening files for writing, and removing, renaming, or changing files, is refused outside the worker's private `HOME` and `TMPDIR`, the output directory, and the project directories listed in `allow_writes`.
- **Databases:** SQLAlchemy `Engine.connect`, `raw_connection` and `begin`, Peewee `Database.connect`, and Tortoise `init` and `generate_schemas` raise instead of connecting. File-backed `sqlite3.connect` is refused. Django's connections are blocked by the Django adapter during extraction.
- **Home directory:** `HOME` and `TMPDIR` point to an empty directory for the worker, so tools that look for `~/.pgpass`, `~/.aws` or `~/.netrc` do not find them. Code that reads those files by absolute path still can at the `process` level.

A blocked operation raises `PermissionError` in the application and is printed as a warning by `build`. If the application does not catch the error, the build fails and says what was blocked.

## Database introspection

The introspection worker reads the database's catalogs. It is a separate process from the extraction worker, and it has the opposite powers: it gets the database URL, but it never imports application code.

**Guaranteed**

- **No application code.** The worker starts with `python -I` from an empty directory. `-I` ignores `PYTHONPATH` and the user's site directory, and the empty directory keeps the project off `sys.path`. Before it returns a result, the worker checks every loaded module, and it fails if any came from the project (environments inside the project are allowed).
- **One worker per connection.** With several connections, each is inspected by its own worker, which receives only that connection's URL.
- **The URL travels on stdin**, not in arguments or the environment. The worker's environment is an allowlist of locale, `PATH` and TLS variables. Errors are scrubbed of the URL and of passwords of 4 or more characters before they leave the worker.
- **Read-only sessions, enforced by the database.** On PostgreSQL every connection has `default_transaction_read_only=on`, and every transaction runs `SET TRANSACTION READ ONLY`. On MySQL and MariaDB the session runs `SET SESSION TRANSACTION READ ONLY`. In both cases the worker reads the setting back and stops if the database does not confirm it. SQLite opens the file with `mode=ro` and `PRAGMA query_only`. The transaction is always rolled back. Statement and lock timeouts are set on PostgreSQL, and statement timeouts on MySQL and MariaDB.
- **Privilege check.** By default, a role that could change the schema or data stops the build (see [database introspection](./database.md#use-a-read-only-role)). `--allow-write-privileges` lets it run anyway: the session is still read-only, and the document records a `write_privileges` diagnostic.

**Best-effort**

- The worker sends only catalog statements: `SELECT`, `WITH`, `SHOW`, session `SET`, read-only `PRAGMA`. Anything else raises before it reaches the driver. This is a second line behind the read-only session, not a replacement for it.
- The MySQL and MariaDB privilege check reads `SHOW GRANTS`. Privileges that reach the user through roles cannot be listed there, so roles are refused rather than trusted. PostgreSQL's check covers role attributes, the database, the inspected schemas and their tables, but not sequences, functions or other schemas.
- A read-only transaction still lets a role read every row it has `SELECT` on. Introspection never does, but only a role without those privileges guarantees it. On MySQL and MariaDB, `information_schema` requires some privilege on each table.

## Upgrading

### From 0.3 to 0.4

Isolated extraction is the default in 0.4. It changes what existing builds see:

- **Environment.** Only allowlisted variables reach your models. Settings that read other variables at import time fail, or get the default. List harmless ones in `env_passthrough`, and give required secrets a placeholder in `[tool.laranex-schema-viewer.env]`, for example `SECRET_KEY = "schema-viewer-placeholder"`.
- **Files.** Imports that write files outside the worker's temporary directories now fail with `PermissionError`. Log directories can be listed in `allow_writes`.
- **Databases and network.** Imports that connect to a database, call a service or start a process at import time now fail with `PermissionError`, where they previously had side effects. The fix belongs in the application: do the work lazily, not at import.
- **Time.** Builds that take longer than 120 seconds need `timeout`.
- **Output.** Isolation does not change what is extracted, for any supported ORM; the test suite compares isolated and in-process output byte for byte.
- **No in-process mode.** If a project cannot build in the `process` worker, fix the import-time side effect, or build at the `container` level with an image that has what the models need. The build never falls back to importing models in-process.
- **Viewer.** `serve` shows the shared Laranex Schema Viewer UI instead of the previous HTML/SVG page. Bookmarked URLs of the old page's assets (`/graph.js`, `/styles.css`) now return 404.
- **Database drivers.** Live introspection needs `pip install 'laranex-schema-viewer[postgresql]'` or `[mysql]`; SQLite needs no extra.

## Remaining risks

- At the `process` level, application code runs as you. It can read any file you can read, including a project `.env` file that a settings module loads. It can also reach the network or write files through native code. If your settings read credentials from files, move them out of the project, or use `container` and leave them out of the image.
- The `container` level is only as strong as the image and the runtime. Use an image that contains your dependencies and this package, and no credentials. Rootless Podman or Docker adds a further layer.
- Extraction still runs mapper configuration, so callbacks registered by your models run inside the worker.
- On Windows, the `process` level stops the worker's process tree with `taskkill`, and the guards are the same.
- A database role's privileges are checked once, when inspection starts. Use a dedicated role for introspection; don't share one with the application.

## Validation

`tests/test_isolation.py` exercises hostile model modules at the `process` level on every run: import-time writes, SQLAlchemy DDL, the raw `sqlite3` driver, mapper callbacks, sockets and DNS, subprocesses, a libc `fork` the guards cannot see, credential discovery and timeouts. `tests/test_introspection.py` and `tests/test_reconcile.py` run against SQLite on every run. When admin URLs for disposable servers are set, they also run against PostgreSQL, MySQL and MariaDB: read-only enforcement with an admin role, privilege refusal, password scrubbing, and introspection accuracy. Container isolation tests run only with `LSV_TEST_CONTAINER_IMAGE` (see `tests/containers/README.md`). They have passed on Docker 29.8 (Docker Desktop on macOS), with an image built from `tests/containers/Dockerfile`. They covered parity with the `process` level, no network for libc-level connects, a read-only project for libc-level writes (`EROFS`), withheld credentials, container removal on timeout, and refusal of an image with another package version. They have not run on Podman, rootless Docker, or a Linux or Windows host. The memory, process-count and capability limits are passed to the runtime but are not tested.

## Configuration

```toml
[tool.laranex-schema-viewer]
isolation = "process"            # or "container"
timeout = 120                    # seconds
env_passthrough = ["APP_ENV"]    # extra variables the worker may see; credentials are refused
allow_writes = ["var/log"]       # project directories the guards let application code write to
container_image = "my-app-schema:latest"
container_runtime = "podman"     # default: docker, then podman

[tool.laranex-schema-viewer.env]
SECRET_KEY = "schema-viewer-placeholder"
```

A container image needs your application's dependencies and the same `laranex-schema-viewer` version as the host. The build checks the version before it runs.

```dockerfile
FROM python:3.12-slim
COPY requirements.txt .
RUN pip install -r requirements.txt laranex-schema-viewer==0.4.0
```
