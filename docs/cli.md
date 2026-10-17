# CLI and configuration

Every option has a default. Command-line options take precedence over the `[tool.laranex-schema-viewer]` section of `pyproject.toml`, which takes precedence over built-in defaults.

## Init

```bash
laranex-schema-viewer init
```

Asks for model modules, recursive import, and the snapshot path (or, in a Django project with `manage.py`, the settings module and optional apps), then appends a `[tool.laranex-schema-viewer]` section to `pyproject.toml`, creating the file if needed. Suggested values come from model detection; press Enter to accept them. `--yes` accepts every suggestion without prompting. `init` never overwrites an existing section and does not import application code.

```toml
[tool.laranex-schema-viewer]
modules = ["app.models"]
recursive = true
output = "schema-viewer.json"
```

| Key | Purpose |
| --- | --- |
| `modules` | Modules to import, like repeated `--module` |
| `targets` | `module:attribute` targets, like repeated `--target` |
| `recursive` | Import every submodule of the selected packages |
| `output` | Snapshot path, relative to the project root; also the default for `serve` and `mcp` |
| `orm` | `auto`, `sqlalchemy`, `django`, `tortoise`, or `peewee`, like `--orm` |
| `settings` | Django settings module, like `--settings` |

## Build

```bash
laranex-schema-viewer build
```

| Option | Default | Purpose |
| --- | --- | --- |
| `--project-root PATH` | Current directory | Application import root and location of `pyproject.toml` |
| `--module MODULE` | Config `modules`, else detected | Import a module; repeatable |
| `--target MODULE:ATTRIBUTE` | Config `targets` | SQLAlchemy metadata, base, or registry; or a Tortoise `TORTOISE_ORM` config |
| `--orm NAME` | Config `orm`, else `auto` | `sqlalchemy`, `django`, `tortoise`, or `peewee`; `auto` detects it |
| `--settings MODULE` | Config `settings`, else `manage.py` | Django settings module; selects Django |
| `--recursive` | Config `recursive`, else off | Discover selected package submodules |
| `--output PATH` | Config `output`, else `schema-viewer.json` | Write the snapshot |
| `--isolation LEVEL` | Config `isolation`, else `process` | Where application code runs: `process` or `container` |
| `--timeout SECONDS` | Config `timeout`, else `120` | Stop extraction after this long |
| `--container-image IMAGE` | Config `container_image` | Image for `--isolation container` |

Application code is imported in a separate extraction worker with a filtered environment: database URLs, passwords and tokens are not passed to it. `env_passthrough`, `[tool.laranex-schema-viewer.env]`, `allow_writes` and `container_runtime` configure it. Operations the worker blocks are printed as warnings. See [build security](./security.md) for what each level guarantees.

When neither options nor configuration select a module, `build` looks up to four directories deep for exactly one `models` package or `models.py` file, skipping hidden, virtual-environment, test, documentation, and build directories. It imports a detected package recursively and prints which module it chose. If it finds none or several, it stops and asks for `--module` or configuration.

Before that, if a `manage.py` in the project root sets `DJANGO_SETTINGS_MODULE`, `build` uses Django with that settings module. See [supported ORMs](./orm-support.md) for how each adapter selects models.

## Serve

```bash
laranex-schema-viewer serve
```

| Option | Default |
| --- | --- |
| `--snapshot PATH` | Config `output`, else `schema-viewer.json` |
| `--host HOST` | `127.0.0.1` |
| `--port PORT` | `8765` |

Keep the loopback default for local inspection. The server serves allowlisted viewer assets and the selected snapshot.

## MCP

```bash
laranex-schema-viewer mcp --snapshot /absolute/path/to/schema-viewer.json
```

`--snapshot` defaults to the configured `output`, else `schema-viewer.json`, read from the current directory's `pyproject.toml`. MCP clients may start the server from another directory, so use an absolute path in client configuration. The server uses stdio and reads a validated snapshot once at startup.

## Help and version

```bash
laranex-schema-viewer --version
laranex-schema-viewer --help
laranex-schema-viewer init --help
laranex-schema-viewer build --help
```
