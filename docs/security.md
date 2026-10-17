# Build security

`build` has to import your models, and importing a module runs its code. Python Schema Viewer runs that code in a separate **extraction worker** and limits what reaches it. This page lists what is guaranteed and what is only best-effort. Keep the two apart when deciding where to run a build.

`serve` and `mcp` read a saved snapshot only. They never import application code or connect to a database, at any isolation level.

## Threat model

The code being imported is your application: models, settings modules, and everything they import, including mapper callbacks and Django `AppConfig.ready()`. Usually it is trusted but careless: an import that opens a database, creates tables, writes a log file, calls a service, or reads `DATABASE_URL`. Sometimes it is a dependency or a pull request you have not reviewed.

The build protects:

- databases, which must not receive connections, DDL, DML, or queries from the extraction step;
- database credentials, which must not reach application code;
- the project and the machine, which should not change because a schema was extracted;
- the network, which should not be reached during extraction.

## Isolation levels

Choose a level with `--isolation` or `isolation` in `[tool.laranex-schema-viewer]`. Both levels run the worker as a separate program with a timeout. When a level cannot be set up (no container runtime, no image, or the wrong package version in the image), the build stops with an error. It never falls back to a weaker level, and there is no in-process level.

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
- **The extraction worker never connects to a database on purpose.** It has no database configuration of its own. Live database introspection is planned as a separate worker that never imports application code, and its credentials will never be passed to the extraction worker.
- **The worker's results are checked.** The worker returns the document through a file of at most 32 MiB, and the parent validates it against the snapshot schema before writing anything. Output on stdout cannot be mistaken for a result. A failed or invalid build leaves the previous snapshot in place.
- **Time is bounded.** A worker that does not finish within `timeout` seconds (default 120) is stopped.

**`process`**

- **The worker and its process group are killed** when it finishes, fails, or times out. Processes left behind by application code are killed with it, including ones started in ways the guards cannot see. A process that leaves the group on purpose (`setsid`, a double fork into a new session) is out of reach. Use `container` for code that might do that.

**`container`**

- **No network:** the container has only a loopback interface.
- **The project is mounted read-only**, and so is the image's root filesystem. Only a size-limited `/tmp` and a disposable output directory can be written. Directories listed in `allow_writes` are still read-only at this level; application code that has to write to them fails.
- **Nothing outlives the build:** the container is removed, or killed on timeout, along with every process in it.
- **Reduced privileges:** all Linux capabilities are dropped, privilege escalation is disabled, and the process count is limited.

## Best-effort safeguards

The worker installs Python audit hooks and patches the ORMs' connect methods before it imports anything. These guards catch most accidental side effects and report them. They run in the same interpreter as the code they watch, so they are **not a security boundary**: native extensions, `ctypes`, and code that restores the patched methods get past them.

- **Network:** socket connects, binds, sends and host name lookups are refused.
- **Processes:** `subprocess`, `os.system`, `exec*`, `spawn*`, `fork`, and `pty.spawn` are refused.
- **Files:** opening files for writing, and removing, renaming, or changing files, is refused outside the worker's private `HOME` and `TMPDIR`, the output directory, and the project directories listed in `allow_writes`.
- **Databases:** SQLAlchemy `Engine.connect`, `raw_connection` and `begin`, Peewee `Database.connect`, and Tortoise `init` and `generate_schemas` raise instead of connecting. File-backed `sqlite3.connect` is refused. Django's connections are blocked by the Django adapter during extraction.
- **Home directory:** `HOME` and `TMPDIR` point to an empty directory for the worker, so tools that look for `~/.pgpass`, `~/.aws` or `~/.netrc` do not find them. Code that reads those files by absolute path still can at the `process` level.

A blocked operation raises `PermissionError` in the application and is printed as a warning by `build`. If the application does not catch the error, the build fails and says what was blocked.

## Remaining risks

- At the `process` level, application code runs as you. It can read any file you can read, including a project `.env` file that a settings module loads. It can also reach the network or write files through native code. If your settings read credentials from files, move them out of the project, or use `container` and leave them out of the image.
- The `container` level is only as strong as the image and the runtime. Use an image that contains your dependencies and this package, and no credentials. Rootless Podman or Docker adds a further layer.
- Extraction still runs mapper configuration, so callbacks registered by your models run inside the worker.
- On Windows, the `process` level stops the worker's process tree with `taskkill`, and the guards are the same.

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
RUN pip install -r requirements.txt laranex-schema-viewer==0.3.1
```
