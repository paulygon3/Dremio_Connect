# Architecture Inventory — Dremio to Excel Exporter

Re-derived from a full read of every source file on **2026-08-19**, against
branch `architecture-mapping` at commit `b4ec5dd` (app version 3.0.0). Every
count, line number and symbol list below was produced mechanically — `wc -l`
for sizes, an AST sweep for definitions, imports and reference counts — not
carried over from the previous edition.

> ## What this supersedes
>
> The previous edition described the **pre-remediation** codebase and carried a
> STALE banner from the Stage 1 merge. Stage 1 changed every source file, so
> that document's counts, line numbers and function lists were all wrong. This
> one replaces them. The correction record is kept rather than erased:
>
> | | Previous edition | Now |
> |---|---|---|
> | `app.py` | 779 | **2,191** |
> | `config.py` | 465 | **878** |
> | `connection.py` | 383 | **615** |
> | `utils.py` | 337 | **620** |
> | `constants.py` | 137 | **258** |
> | application total | 2,415 | **4,620** |
> | test suite | not mentioned | **32 files, 7,508 lines** + a 211-line README |
> | `requirements.txt` | 5, unbounded `>=` floors | **6, pinned exactly** (F-32) |
> | `dremio_Tool/README.md` | 241 lines | **304** |
>
> Its **§5 "Observations affecting how the code runs" is superseded** by
> [`AUDIT.md`](AUDIT.md), where all 33 findings carry a `### Status:` block
> recording what was actually done. Where a finding body and its status block
> disagree, the status block is what happened. §6 below keeps only the
> observations that are still true and are *structural* rather than defects.
>
> What survived unchanged is the **shape**: nine application files in one flat
> package, one orchestrator, clean acyclic layering, and the runtime seams no
> import graph shows. §4 and §5 are new and record those seams explicitly.

---

## 1. Directory tree

Excludes `.git/` and `__pycache__/`. No `node_modules`, `dist`, `build`,
`.venv`, `vendor`, or lockfiles exist in this repository.

```
Dremio_To_Excel/
├── CLAUDE.md                  # Working instructions for this repo
├── README.md                  # 2-line repo stub
├── docs/architecture/
│   ├── AUDIT.md               # 34 findings, each with a Status block
│   └── INVENTORY.md           # This file
└── dremio_Tool/               # The entire application
    ├── main.py                # Entry point (36)
    ├── app.py                 # GUI + orchestration (2,191)
    ├── config.py              # Persistence: settings, history, credentials, query library (878)
    ├── connection.py          # Arrow Flight client + auth middleware (615)
    ├── utils.py               # Asset discovery, validation, string helpers (620)
    ├── constants.py           # Colors, defaults, limits, metadata (258)
    ├── __init__.py            # Package re-exports (22) — see §6.1, does not work
    ├── requirements.txt       # 6 dependencies, pinned exactly
    ├── README.md              # User-facing docs (304)
    ├── assets/
    │   ├── logo.png           # Header logo, resized to 45×45
    │   └── logo.ico           # Window icon
    └── tests/                 # 32 .py files, 7,508 lines — added by Stage 0/1
        ├── README.md          # How the suite works (211)
        ├── run_all.py         # Runs every repro in a subprocess, prints a summary (192)
        ├── harness.py         # Shared fixtures: isolated HOME, Tk app, dialogs, RSS (584)
        ├── flightserver.py    # Local Arrow Flight server, so tests need no Dremio (240)
        └── repro_f*.py        # 29 reproduction scripts, one or two findings each
```

**Application total: 4,620 lines across 7 `.py` files.** The 29 repro scripts
cover 31 findings (`repro_f01_f02_column_letters.py` and
`repro_f09_f10_resource_release.py` each cover two). F-20 and F-23 have no
script of their own: F-20 shares its fix and its test with F-22, and F-23 is
covered inside the validation repros. `AUDIT.md` §Method records this.

**Layout note (unchanged, still true):** the folder is `dremio_Tool`, but
`main.py`'s docstring and `dremio_Tool/README.md` both call the project
`dremio_exporter`. That name does not exist on disk.

## 2. Entry points, frameworks, and how the app starts

### Entry point

`dremio_Tool/main.py`, run as `python main.py` **with the working directory set
to `dremio_Tool/`** — or with `dremio_Tool/` on `sys.path`, which is what
`harness.add_src_to_path()` does for the tests. See §6.1.

### Startup sequence

```
main()                                   main.py:23
  tk.Tk()                                creates the root window
  DremioExporter(root)                   app.py:63
    ConfigManager()                      config.py:157
      _setup_directories()               config.py:173 — mkdir app dir 0o700 + chmod,
                                                         mkdir saved_queries/
      _load_config()                     config.py:205 — read, coerce shape, migrate, merge
      _load_history()                    config.py:266 — read, coerce to list-of-dicts
    DremioConnection()                   connection.py:195 — inert, no I/O
    root.geometry(...)                   size restored from config
    _set_window_icon() → load_icon(root) utils.py:218 — scans for *.ico
    load_logo_image()                    utils.py:186 — needs Pillow, else None
    threading.Event()                    app.py:95  — the cancel flag
    queue.Queue()                        app.py:112 — the UI marshalling queue
    _setup_styles()                      app.py:133 — ttk 'clam' theme
    _create_header() / _create_main_content() / _create_status_bar()
      _create_log_panel                  app.py:431 — drains config.load_warnings (468),
                                                      installs config.on_warning (486)
    _load_saved_settings()               app.py:515 — reads token via config.get_token
    protocol("WM_DELETE_WINDOW", ...)     app.py:124
    root.after(50, _drain_ui_queue)      app.py:127 — starts the UI pump
  root.mainloop()                        main.py:32
```

**No network activity occurs at startup.** The Dremio connection is established
only on Connect and queries run only on Execute, both on daemon threads. The UI
pump started at `app.py:127` reschedules itself every `UI_QUEUE_POLL_MS` (50 ms)
for the life of the window — see §5.

### Frameworks and external dependencies

| Package | Required? | Used for | Used in |
|---|---|---|---|
| `tkinter` / `ttk` | stdlib | Entire GUI | `main.py`, `app.py`, `utils.py` (`iconbitmap` only) |
| `pyarrow` (+ `pyarrow.flight`) | yes | Arrow Flight client, auth middleware, batch reads, decimal→float cast | `connection.py` |
| `pandas` | yes | Result frame, dtype handling in the write loop | `app.py`, `connection.py` (return type) |
| `openpyxl` | yes | `Workbook(write_only=True)`, `get_column_letter`, `ILLEGAL_CHARACTERS_RE` | `app.py` — **direct**, not via pandas, since F-08 |
| `numpy` | yes | `_excel_value` recognises numpy scalars by type | `app.py:22` — direct import, declared since F-08 |
| `Pillow` | optional | Logo load/resize; text fallback if absent | `utils.py:196` (function-local import) |
| `keyring` | optional | OS credential store; base64 file fallback if absent | `config.py:47` (guarded import) |

Both optional dependencies degrade gracefully via `try/except ImportError`.
Installing `keyring` is not the same as having a working backend — on Linux it
commonly resolves to `keyring.backends.fail.Keyring`, which makes the base64
file the live path there rather than a rare branch (F-28).

`pandas` is no longer in the Excel **write** path: `df.to_excel` was replaced by
a `write_only` workbook and an explicit append loop (F-08).

### Config / build files

- `requirements.txt` — the only dependency manifest, now 6 exact pins with the
  reasoning recorded inline. No `setup.py`, `pyproject.toml`, `Makefile`,
  linter config or CI. `repro_f32` checks each pin against what is installed,
  so it doubles as a drift detector for every other verdict in the suite.
- Packaging is documented but not scripted: `dremio_Tool/README.md` gives a
  manual PyInstaller one-liner.

### Runtime-created files (not in the repo)

Created under `%APPDATA%/DremioExporter/` (Windows) or `~/.dremioexporter/`
(POSIX). The directory itself is created `0o700` and `chmod`ed to `0o700`
afterwards, because the mode passed to `mkdir` is masked by the umask
(`config.py:188-194`).

| File | Written by | Contents |
|---|---|---|
| `config.json` | `save_config` → `_save_json` | Connection, output, UI settings + a `meta.config_version` stamp |
| `query_history.json` | `save_history` → `_save_json` | Last 20 queries + timestamps + labels |
| `.credentials` | `_save_token_to_file` → `_save_json(private=True)` | **Base64-encoded PAT** — obfuscation, not encryption (F-28, open by decision) |
| `saved_queries/*.sql` | `save_query_file` | The query library — **reachable from the UI since F-31** |

All three JSON files are written atomically: temp sibling → `flush` → `fsync` →
`os.replace` (`config.py:423-436`). `.credentials` gets its temp file via
`os.open(..., 0o600)` plus an explicit `chmod`, because the rename installs
*that* inode (`config.py:734-758`).

## 3. Per-file inventory

---

### `dremio_Tool/main.py` — 36 lines

**Purpose:** Application entry point; builds the Tk root and starts the loop.

- **Exports:** `main()`
- **Internal imports:** `app` (absolute: `from app import DremioExporter`)
- **External:** `tkinter`
- **Side effects:** creates the Tk root; `root.mainloop()` blocks.
- **Note:** the docstring still advertises `python -m dremio_exporter`. No such
  module exists and module-style execution fails regardless — §6.1.

---

### `dremio_Tool/__init__.py` — 22 lines

**Purpose:** Package-level re-exports and metadata.

- **Exports:** `DremioExporter`, `ConfigManager`, `DremioConnection`,
  `__version__ = '3.0.0'`, `__author__`
- **Internal imports:** `.app`, `.config`, `.connection` — **relative**, unlike
  every other file
- **External:** none
- **Status:** all three imported names are referenced **0×**, and the module
  cannot execute at all. This is the only fully dead import edge in the
  codebase — §4 bucket (b), §6.1.

---

### `dremio_Tool/constants.py` — 258 lines

**Purpose:** Single source of truth for metadata, colors, defaults, limits and
filenames. Much of the file is now commentary explaining *why* a limit is what
it is; the Stage 1 fixes put their reasoning next to the value.

- **Exports (37 module constants):** `APP_NAME`, `APP_VERSION`, `APP_TITLE`,
  `APP_SUBTITLE`, `COPYRIGHT`, `WINDOW_MIN_WIDTH/HEIGHT`,
  `WINDOW_DEFAULT_WIDTH/HEIGHT`, `COLORS` (18 keys), `CONFIG_VERSION`,
  `LEGACY_SHEET_NAME`, `DEFAULT_CONFIG`, `EXCEL_MAX_CELL_CHARS`,
  `EXCEL_MAX_SHEET_ROWS`, `EXCEL_MAX_DATA_ROWS`, `EXCEL_MAX_SHEET_NAME`,
  `EXCEL_FORBIDDEN_SHEET_CHARS`, `EXCEL_RESERVED_SHEET_NAME`,
  `DEFAULT_SHEET_NAME`, `TRUNCATION_REPORT_LIMIT`, `ILLEGAL_CHAR_REPLACEMENT`,
  `SANITISED_REPORT_LIMIT`, `UI_QUEUE_POLL_MS`, `MAX_QUERY_HISTORY`,
  `HISTORY_LABEL_LENGTH`, `HISTORY_LABEL_ELLIPSIS`, `QUERY_FILE_ENCODINGS`,
  `TEXT_FILE_ENCODING`, `DEFAULT_QUERY`, `ROUTING_TAG`, `SSL_CERT_NAME`,
  `CONFIG_FILENAME`, `HISTORY_FILENAME`, `CREDENTIALS_FILENAME`,
  `SAVED_QUERIES_FOLDER`, `ASSETS_FOLDER`, `LOGO_SIZE`
- **New since the last edition:** every `EXCEL_*` limit, `CONFIG_VERSION`,
  `LEGACY_SHEET_NAME`, `UI_QUEUE_POLL_MS`, `HISTORY_LABEL_*`,
  `QUERY_FILE_ENCODINGS`, `TEXT_FILE_ENCODING`, `ILLEGAL_CHAR_REPLACEMENT`,
  `SANITISED_REPORT_LIMIT`, `TRUNCATION_REPORT_LIMIT`, `DEFAULT_SHEET_NAME`
- **Internal imports:** none — this is the dependency root
- **External:** none. **Side effects:** none. Pure data.
- **Site-specific values:** `ROUTING_TAG = b"lid-toolbox-default-tag"` and
  `SSL_CERT_NAME = 'RWE Server Auth Issuing CA'` are hardcoded to one
  organisation's Dremio deployment.
- **Referenced nowhere else:** `ASSETS_FOLDER`. `WINDOW_DEFAULT_WIDTH/HEIGHT`
  are referenced only inside `DEFAULT_CONFIG` — they reach `app.py` as *values*
  through `config.get('ui', 'window_width', …)`, never as symbols (§4, c12).
- **Declared but never read** (down from 5 to 4): `DEFAULT_CONFIG` keys
  `include_timestamp`, `apply_table_format`, `table_style`, `auth_method`.
  `sheet_name` **is now read** (F-07) at `app.py:1810`, which is what made
  `CONFIG_VERSION` / `LEGACY_SHEET_NAME` and the migration necessary.

---

### `dremio_Tool/config.py` — 878 lines

**Purpose:** Persistent storage of settings, query history, credentials, and the
saved-query library.

- **Exports:** `ConfigManager`, `KEYRING_AVAILABLE`, `build_query_label`, and
  the module-private `_marked_label`, `_label_timestamp`
- **`ConfigManager` public API (17):** `save_config`, `reset_config`, `get`,
  `set`, `save_history`, `add_to_history`, `clear_history`,
  `get_history_labels`, `get_query_from_history`, `get_token`, `save_token`,
  `delete_token`, `get_saved_queries`, `clean_query_name` (static),
  `save_query_file`, `load_query_file`, `delete_query_file`
- **Internal machinery (12):** `_setup_directories`, `_load_config`,
  `_migrate_config`, `_load_history`, `_coerce_config`, `_coerce_history`,
  `_get_default_config`, `_merge_with_defaults`, `_load_json`, `_save_json`,
  `_warn`, `_open_private`, plus `_get_token_from_file`, `_save_token_to_file`,
  `_delete_token_from_file`
- **Public attributes:** `load_warnings` (list), `on_warning` (callback slot),
  `config`, `history`, `app_dir`, `config_file`, `history_file`,
  `credentials_file`, `queries_dir`
- **Internal imports:** `constants` (12 names, all used)
- **External:** `keyring` (optional, guarded), stdlib `os`, `json`, `base64`,
  `pathlib`, `datetime`, `copy`
- **Side effects:**
  - **Env var:** reads `APPDATA` (`config.py:177`), Windows branch only
  - **File I/O:** `mkdir` 0o700 + `chmod` on the app dir and `mkdir` on
    `saved_queries/` at construction; atomic read/write of `config.json`,
    `query_history.json`, `.credentials`; reads, writes, globs and **unlinks**
    `.sql` files
  - **Credential store:** `keyring.get_password` / `set_password` /
    `delete_password` under service name `DremioExporter`
  - **Global state:** module-level `KEYRING_AVAILABLE` set at import
  - **Reporting:** `print()` is **gone** from this module (F-20). Problems go to
    `load_warnings` before the UI exists and through `on_warning` afterwards —
    §4, c3.
- **Security:** the `.credentials` fallback is base64, which is encoding, not
  encryption. The code says so at `config.py:768`; `dremio_Tool/README.md` now
  says so too. Permissions are fixed (0o600 via `_open_private`); **the
  reversible encoding is open by decision** and is the one CONFIRMED verdict in
  a clean suite run.
- **No callers anywhere:** `reset_config`, `clear_history`. Called only from
  inside the module: `save_history` (by `add_to_history` / `clear_history`),
  `build_query_label`.

---

### `dremio_Tool/connection.py` — 615 lines

**Purpose:** Arrow Flight transport — auth middleware, TLS setup, cancellable
query execution. **Never imports the UI.**

- **Exports:** `QueryCancelled` (new), `DremioClientAuthMiddleware`,
  `DremioClientAuthMiddlewareFactory`, `get_ssl_certificate()`,
  `DremioConnection`
- **`DremioConnection` API:** `connect(hostname, port, username, token, use_tls,
  on_status)`, `disconnect()`, `execute_query(query, on_status, cancel_event)`,
  `cancel_query()` (new), `connection_string` (property), attrs `is_connected`,
  `client`, `bearer_token`, `middleware`, `hostname`, `port`
- **Internal machinery:** `_test_connection`, `_release_client` (new),
  `_read_stream` (new), `_arrow_to_pandas`; state `_active_reader` +
  `_reader_lock` (new)
- **Internal imports:** `constants` (`ROUTING_TAG`, `SSL_CERT_NAME`)
- **External:** `pyarrow`, `pyarrow.flight`, stdlib `ssl`, `codecs`,
  `threading`
- **Side effects:**
  - **Network:** `FlightClient` (295), `authenticate_basic_token` (308),
    `get_flight_info` (338, 446), `do_get` (342, 456), `read_chunk` (501) — the
    only outbound network calls in the codebase
  - **TLS:** `ssl.create_default_context().get_ca_certs()` reads the system
    trust store; on miss, sets `disable_server_verification=True`
  - **stdout:** one `print` left, on certificate-extraction failure (158)
  - **State:** mutates instance connection state; the middleware factory holds
    the bearer token across calls; `_active_reader` is published for the
    duration of a stream read
- **Auth flow:** Dremio returns a bearer token in the `authorization` response
  header; `DremioClientAuthMiddleware.received_headers` captures it into the
  factory, and it is replayed as a `FlightCallOptions` header on every later
  RPC. Neither hook has an in-repo caller — §4, c8.
- **Read path (changed by F-13):** `execute_query` no longer calls `read_all()`.
  It binds `client`/`bearer_token` to locals (F-14), publishes the reader under
  a lock, and delegates to `_read_stream`, which loops on `read_chunk()`,
  checks `cancel_event` between batches, reports progress per batch, and
  **discards every batch on cancellation** — a partial workbook would be
  indistinguishable from a complete one. `reader.cancel()` runs on every path
  that did not end by exhaustion (F-10).
- **Hostname contract (F-23/F-24):** this module **checks** rather than cleans.
  A hostname carrying a scheme, a path or whitespace raises `ValueError` here;
  normalising is `utils.clean_hostname`'s job, deliberately not imported because
  `utils` pulls in tkinter.
- **Data handling:** `_arrow_to_pandas` rewrites the schema to cast
  `decimal128` → `float64` before `to_pandas(split_blocks=True,
  self_destruct=True)`. That precision loss is deliberate and still undocumented
  in the README.
- **Security note:** when the named CA is not found, verification is disabled
  and reported only as a status string.
- **No callers anywhere:** `connection_string`. Called only inside the module:
  `get_ssl_certificate`, `set_call_credential`.

---

### `dremio_Tool/utils.py` — 620 lines

**Purpose:** Asset discovery, image loading, validation, string helpers.

- **Exports (15):** `get_script_directory`, `get_asset_path`, `get_logo_path`,
  `get_icon_path`, `_find_asset_by_extension`, `list_assets`, `load_logo_image`,
  `load_icon`, `truncate_string`, `generate_timestamp_filename`,
  `validate_connection_params`, `validate_output_filename` (new),
  `validate_sheet_name` (new), `clean_hostname`, `_hostname_error` (new); plus
  the compiled patterns `_SCHEME_RE`, `_LABEL_RE`, `_PORT_RE`
- **Internal imports:** `constants` (`LOGO_SIZE`, `EXCEL_MAX_SHEET_NAME`,
  `EXCEL_FORBIDDEN_SHEET_CHARS`, `EXCEL_RESERVED_SHEET_NAME`)
- **External:** `PIL` (optional, imported inside `load_logo_image`), stdlib
  `ipaddress`, `re`, `sys`, `pathlib`, `datetime`
- **Side effects:**
  - **File I/O:** globs and stats four candidate directories (script dir,
    `script/assets`, cwd, `cwd/assets`); opens image files
  - **Frozen-binary awareness:** `get_script_directory` branches on `sys.frozen`
  - **Tk mutation:** `load_icon` calls `root.iconbitmap(...)` — the only Tk call
    outside `app.py`
  - **stdout:** 9 `print` calls, all asset-loading diagnostics. F-20 deliberately
    scoped its fix to the persistence layer ("the 6 in `config.py` are the ones
    that matter"), so these remain by decision, not by oversight.
- **Validation contract (F-24):** `validate_connection_params` returns
  `(is_valid, error, params)` and **callers must use `params`**, not what they
  passed in — the canonical hostname and port are the ones that were checked.
  `app.py:1178-1181` does exactly that.
- **Platform note:** `root.iconbitmap()` with an `.ico` path is Windows-only in
  practice.
- **No callers in the application:** `get_asset_path`, `list_assets`, and
  `truncate_string` — though `truncate_string` is exercised by 4 test files
  (F-26 fixed a latent bug in it rather than a live one). `clean_hostname` and
  `_hostname_error` are now called only from `validate_connection_params`;
  **`app.py` no longer imports `clean_hostname`**, contrary to the previous
  edition.

---

### `dremio_Tool/app.py` — 2,191 lines

**Purpose:** The `DremioExporter` GUI class — layout, event handling, threading,
UI marshalling, and the whole Excel export path. Sole orchestrator; every other
module is a leaf it calls.

- **Exports:** `DremioExporter` (60 methods)
- **Internal imports:** `constants` (19 names), `config` (`ConfigManager`),
  `connection` (`DremioConnection`, `QueryCancelled`), `utils`
  (`load_logo_image`, `load_icon`, `validate_connection_params`,
  `validate_sheet_name`, `generate_timestamp_filename`,
  `validate_output_filename`)
- **External:** `tkinter` (+ `ttk`, `messagebox`, `scrolledtext`, `filedialog`,
  `simpledialog`), `pandas`, `numpy`, `openpyxl` (`Workbook`,
  `get_column_letter`, `ILLEGAL_CHARACTERS_RE`), stdlib `codecs`, `os`, `sys`,
  `pathlib`, `datetime`, `threading`, `queue`, `warnings`, `weakref`,
  `decimal`, `math`
- **Methods by area:**
  - *Construction:* `__init__`, `_set_window_icon`, `_setup_styles`
  - *Layout:* `_create_header`, `_create_main_content`,
    `_create_connection_panel`, `_create_output_panel`, `_create_query_panel`,
    `_create_log_panel`, `_create_status_bar`
  - *Settings:* `_load_saved_settings`, `_save_current_settings`,
    `_on_username_change`, `_on_close`
  - *UI marshalling (new, F-12):* `_ui`, `_drain_ui_queue`
  - *UI helpers:* `_log`, `_clear_log`, `_write_text_file`, `_save_log`,
    `_clear_query`, `_read_query_file`, `_put_query_in_editor`, `_browse_output`,
    `_open_output_folder`, `_update_history_dropdown`, `_load_from_history`,
    `_update_connection_status`, `_set_status`
  - *Query library (new, F-31):* `_open_query_library`, `_library_load`,
    `_library_delete`, `_browse_query_file`, `_save_query_file`
  - *Connection:* `_toggle_connection`, `_connect`, `_connect_thread`,
    `_disconnect`, `_set_connecting_state`, `_forget_token_in_form`
  - *Execution:* `_execute_and_export`, `_execute_thread`, `_stop_execution`,
    `_set_executing_state`, `_start_worker`, `_open_exported_file`
  - *Export (largely new):* `_export_to_excel`, `_excel_value`,
    `_column_widths`, `_check_row_ceiling`, `_find_oversized_cells`,
    `_sanitise_illegal_characters`, `_write_truncation_sidecar`,
    `_record_truncation`, `_sanitised_report_lines`, `_resolve_export_path`,
    `_resolve_sheet_name`, `_snapshot_export_settings`, `_discard_partial_export`
- **Side effects:**
  - **Network:** indirect, via `DremioConnection` on two daemon threads
  - **File I/O:** `mkdir(parents=True)` on the output dir; writes `.xlsx`, the
    `.truncated.txt` sidecar, `.txt` logs; reads `.sql` files as **bytes** and
    decodes by BOM then by `QUERY_FILE_ENCODINGS` (F-18); deletes a partial
    workbook on a failed export (F-11)
  - **OS shell:** `os.startfile` at `app.py:1031` and `app.py:1352` — Windows
    only, and both now **guarded** by `hasattr(os, 'startfile')` with a
    non-Windows message instead of an exception (F-16, F-17)
  - **Threading:** two daemon threads; **no worker touches Tcl** — every update
    goes through `_ui()` onto a queue (§5)
  - **Global state:** `self.df` holds the result frame but is cleared on every
    path in `_execute_thread`'s `finally` (F-08); `self.cancel_requested` is a
    `threading.Event`; `warnings.catch_warnings` manipulates global state during
    the write and is safe only because Execute is disabled for the duration
  - **Credentials:** writes the PAT on connect when "Remember token" is ticked,
    **deletes** it when unticked (F-29), and clears the entry widget after a
    successful connect when it is not being kept (F-30)
- **Excel export detail:** `Workbook(write_only=True)` + an explicit append loop,
  not `df.to_excel` (F-08). Column widths and `freeze_panes = 'A2'` must be set
  before the first row. Column letters come from openpyxl's
  `get_column_letter` (F-01). Sheet name comes from config and is validated
  (F-07). Row ceiling is checked before anything is written (F-04). Over-length
  cells are found before the write and preserved in a sidecar (F-03); control
  characters are stripped and reported (F-05); both counts are reconciled
  against what the write loop actually handed to openpyxl.

---

### `dremio_Tool/tests/` — 32 files, 7,508 lines

Not unit tests: one **reproduction script per finding**, each printing a
machine-readable `VERDICT|…` line that `run_all.py` collects.

| File | Role |
|---|---|
| `run_all.py` (192) | Runs each script in its own subprocess; wraps scripts declaring `REQUIRES_DISPLAY = True` in `xvfb-run -a` when `$DISPLAY` is unset; `--only`, `--list`, `-v`, `--timeout` |
| `harness.py` (584) | 28 shared helpers: `add_src_to_path`, `isolated_home`, `app_data_dir`, `tk_app`, `run_with_mainloop`, `pump`, `wait_for`, `captured_dialogs`, `chosen_file`, `named_save`, `captured_stderr`, `silence_fd_stderr`, `grep_source`, `peak_rss_mb`, `current_rss_mb`, `StubConnection`, … |
| `flightserver.py` (240) | `ReproFlightServer`, `connected_connection`, `server_subprocess` — a local Arrow Flight server, so no repro needs a live Dremio |
| `repro_f*.py` (29 files) | One or two findings each |

**How the tests reach the source:** `harness.add_src_to_path()` puts
`dremio_Tool/` on `sys.path`, then the repros use the same absolute imports the
app does — `harness` (29 files), `flightserver` (7), `config` (5),
`connection` (4), `constants` (4), `utils` (4), `app` (2). This is a real
consumer of the modules and the reason "no callers in the application" and "dead"
are not the same claim (see `truncate_string`).

A clean run is **`32 NOT REPRODUCIBLE, 1 CONFIRMED`**; the CONFIRMED one is
F-28's encoding half and is expected.

## 4. Edge classification

Every edge in the codebase, in three buckets:

- **(a)** import edge that is also a real call → solid arrow
- **(b)** import edge that is dead → greyed out, labelled "unused"
- **(c)** runtime edge with no import behind it → dashed arrow

> **A note for anyone reading the older documents.** `root.after(0, ...)` **no
> longer exists anywhere in this codebase.** F-12 replaced all of those direct
> cross-thread calls with a `queue.Queue` plus one self-rescheduling pump.
> `grep -n "\.after(" dremio_Tool/*.py` returns three hits: `app.py:127` and
> `app.py:666`, which are the pump, and `app.py:617`, which is a docstring line
> describing the mechanism that was removed.
> Parts of `AUDIT.md` still describe the old
> mechanism; the runtime edges are still there and still load-bearing, but the
> marshalling underneath them changed.

### (a) Import edges that are real calls

Verified by counting `Name`/`Attribute` loads of each imported symbol inside the
importing module. Every internal import edge in the application is live.

| From | To | Symbols | Evidence |
|---|---|---|---|
| `main.py` | `app` | `DremioExporter` | constructed at `main.py:29` |
| `app.py` | `constants` | 19 names | all used; `COLORS` 59×, `EXCEL_MAX_CELL_CHARS` 7× |
| `app.py` | `config` | `ConfigManager` | constructed `app.py:75`; **39 call sites** |
| `app.py` | `connection` | `DremioConnection`, `QueryCancelled` | constructed `app.py:76`; 5 calls; exception raised 1378, caught 1452 |
| `app.py` | `utils` | 6 names | 6 call sites (88, 131, 1171, 1811, 1832, 1836) |
| `config.py` | `constants` | 12 names | all used |
| `connection.py` | `constants` | `ROUTING_TAG`, `SSL_CERT_NAME` | 1× / 2× |
| `utils.py` | `constants` | 4 names | all used |

Acyclic, single direction, `constants.py` a pure leaf. External edges are listed
in §2; the ones worth drawing are `connection.py → pyarrow.flight` and
`app.py → openpyxl` (direct since F-08, no longer through pandas).

### (b) Import edges that are dead

**Fully dead — one file.** `__init__.py`'s three relative imports (`.app`,
`.config`, `.connection`). All three names are referenced 0×, and the module
cannot execute at all: `python3 -c "import dremio_Tool"` →
`ModuleNotFoundError: No module named 'constants'`. Mark it "unused"; do not
omit it. See §6.1.

**An edge that no longer exists.** The previous edition recorded
`app.py → utils.clean_hostname`. That import is gone — `clean_hostname` is now
called only from `validate_connection_params` inside `utils.py` (F-24). Drawing
it from the old inventory would draw a relationship that does not exist.

**Live edge, dead symbol behind it.** The honest home for F-31's remainder:

| Symbol | Refs in app | Refs in tests |
|---|---|---|
| `utils.get_asset_path`, `utils.list_assets` | 0 | 0 |
| `utils.truncate_string` | 0 | 4 files |
| `config.reset_config`, `config.clear_history` | 0 | 0 |
| `connection.connection_string` | 0 | 0 |
| `constants.ASSETS_FOLDER` | 0 | 0 |
| `DEFAULT_CONFIG` keys `include_timestamp`, `apply_table_format`, `table_style`, `auth_method` | written every save, never read | 0 |

**No longer in this bucket** — F-31 and F-29's status blocks, not their finding
bodies, are what is true: `config.delete_token` (`app.py:574`), all four
saved-queries methods (`get_saved_queries` 862, `save_query_file` 983,
`load_query_file` 802, `delete_query_file` 837), and the `sheet_name` config key
(`app.py:1810`).

**Not dead despite zero in-repo callers:** `received_headers` and `start_call` —
see (c8). An AST sweep flags them; deleting them breaks authentication.

### (c) Runtime edges with no import behind them

| # | Edge | Assigned | Invoked | Threads |
|---|---|---|---|---|
| c1 | `on_status`, connect path | `app.py:1223` — `lambda msg: self._ui(lambda: self._log(msg))`, passed as a keyword to `connection.connect` | wrapped as local `status()` `connection.py:228-230`; called at 272, 278, 281, 284, 287, 292, 302, 319, 322, 326 (**10 sites** — corrected 2026-08-19; this row previously said 11 while listing these same ten. `connection.py:228-230` is the wrapper *definition*, not a call) | raised on the **connect worker**, consumed on the **Tk thread**. `app.py:1223` is the keyword argument inside `connection.connect(...)` in `_connect_thread` (def 1199), so it is evaluated **on the worker**, not on Tk |
| c2 | `on_status`, execute path | `update_progress` defined `app.py:1363`, passed `app.py:1369` | wrapped `connection.py:425-427`; called at 445, 455, 466, and **once per record batch** at 517 — reached there as a bare parameter of `_read_stream`, a second hop with no import | **execute worker** → **Tk thread**; the highest-frequency edge in the app |
| c3 | `config.on_warning` | slot declared `config.py:167`; assigned `app.py:486` to a **weakref-guarded** closure | `config._warn` `config.py:457-459`, reached from 6 sites: `_save_json` 438, `get_token` 668, `save_token` 692, `_get_token_from_file` 730, `_save_token_to_file` 775, `_delete_token_from_file` 790 | Tk thread in every current path (see note below) |
| c3b | `config.load_warnings` | list created `config.py:163`; appended at 192, 291, 303, 326, 336, 387, 464 | drained `app.py:468`, once, as the log panel is built | Tk thread; covers the window before `on_warning` exists |
| c4 | the UI queue | `queue.Queue()` `app.py:112`; producers call `_ui()` at **32 sites** — `_execute_thread` 18, `_connect_thread` 10, `_open_exported_file` 2, `_discard_partial_export` 1, the config-warning closure 1 | `_drain_ui_queue` `app.py:640` executes each callable | producer **any thread**, consumer **Tk thread** |
| c5 | the UI pump | `root.after(UI_QUEUE_POLL_MS, self._drain_ui_queue)` at `app.py:127` (start) and `app.py:666` (reschedule) | Tk event loop, every 50 ms | Tk thread only. **These are the only two `.after()` calls in the codebase** — the third grep hit, `app.py:617`, is a docstring |
| c6 | the cancel `Event` | `threading.Event()` `app.py:95`; cleared `app.py:1314`; **set** by `_stop_execution` 2176 and `_on_close` 602; passed into `execute_query` at 1370 | read at **two different points, and only one of them is between batches** (corrected 2026-08-19, this row previously described both that way): `connection.py:451` is a **pre-stream guard** inside `execute_query` (def 403), between `get_flight_info` and `do_get` — it raises `QueryCancelled("Cancelled before the result stream was opened")` at 452 and is reached before any batch exists; `connection.py:496` is the **in-loop** check inside `_read_stream` (def 471), which sets `cancelled = True` at 497 and breaks. Re-checked `app.py:1376` before the export | set on the **Tk thread**, read on the **execute worker** |
| c7 | Tk thread interrupting a blocked read | `connection.cancel_query()` from `_stop_execution` 2181, `_on_close` 603, `disconnect` 395 (via `_disconnect` 1108), and a self-call at `connect` 261 | reads `_active_reader` under `_reader_lock` `connection.py:568` — published by the worker at 458 — and calls `reader.cancel()`, so the worker's blocked `read_chunk()` raises `FlightCancelledError`, caught at 505 | **Tk thread → pyarrow object the execute worker is blocked in** |
| c8 | pyarrow calling into our code | none — `DremioClientAuthMiddlewareFactory.start_call` (100) and `DremioClientAuthMiddleware.received_headers` (60) | invoked per RPC by pyarrow's Flight machinery; `received_headers` → `set_call_credential` is what makes every later query authenticate | on whichever **worker** issues the RPC |
| c9 | Tk event loop → handlers | 13 bindings on the main window: `protocol("WM_DELETE_WINDOW", _on_close)` 124, `<FocusOut>` → `_on_username_change` 282, `<<ComboboxSelected>>` → `_load_from_history` 375, and 10 `command=` buttons — **mapped to their labels 2026-08-19, because the bare list was being mis-read positionally**: 309 Connect → `_toggle_connection`, 332 "..." browse → `_browse_output`, 382 Library → `_open_query_library`, 384 Save → `_save_query_file`, 386 Clear → `_clear_query`, 411 **Execute** → `_execute_and_export`, 421 **Stop** → `_stop_execution`, 456 Clear Log → `_clear_log`, 458 Save Log → `_save_log`, 460 Open Output Folder → `_open_output_folder` | plus 5 inside the Query Library `Toplevel` — 4 buttons (909, 910, 912, 914) and a double-click (916) | Tk thread |
| c10 | exception routing | `_drain_ui_queue` sends a raising callback to `root.report_callback_exception` `app.py:661` | — | Tk thread; same destination an `after()` callback's exception would reach |
| c11 | test-side substitution | `harness.StubConnection` duck-types `DremioConnection` **without importing `connection.py`**; `flightserver.ReproFlightServer` stands in for Dremio | in 7 repro scripts | test process |
| c12 | config *value* edges | `constants.DEFAULT_CONFIG` keys reach `app.py` only as strings — e.g. `WINDOW_DEFAULT_WIDTH` → `'ui'/'window_width'` → `config.get('ui', 'window_width', 1100)` at `app.py:79` | — | no symbol edge exists; a rename in `constants.py` fails silently into the default |

**On c3, an observation worth recording.** All 39 `ConfigManager` call sites in
`app.py` are in Tk-thread methods — `_connect_thread` reaches config only by
queueing `self._ui(self._save_current_settings)` — so `on_warning` currently
fires on the Tk thread in every path. The `_ui` hop inside the callback is
therefore defensive rather than load-bearing today, and `app.py:473`'s comment
("saves also happen on worker threads") overstates the present code. It is the
right defence to keep: the callback's *contract* is that it may be called from
anywhere, and `config.py` cannot know who is calling.

**Why the weakref at `app.py:481` is not decoration.** A closure over `self`
completes the cycle `app → config → callback → app`. A cycle is freed by the
cyclic GC rather than by refcount, and that can run on **any** thread —
finalising this app's Tk variables off the Tk thread and aborting the process
with `Tcl_AsyncDelete: async handler deleted by the wrong thread`. Observed, not
theorised: it killed two repro scripts before the weakref went in.

## 5. Threading model

Three threads, and the app is written so that only one of them ever touches Tcl.

| Thread | Created | Owns |
|---|---|---|
| **Tk main** | `main.py:26`, `mainloop()` at 32 | every widget, the UI pump, all dialogs, all `ConfigManager` calls |
| **connect worker** (daemon) | `app.py:1188`, started via `_start_worker` | `connection.connect` — TLS, auth, test query |
| **execute worker** (daemon) | `app.py:1322`, started via `_start_worker` | `execute_query`, the batch read, the entire Excel write |

`_start_worker` (`app.py:1135`) exists because both handlers disable their
buttons *before* starting the thread and only the worker's `finally` re-enables
them — so a `start()` that raises would latch the UI permanently (F-19).

**Worker → Tk** is one-way, through the queue: the worker puts a callable, the
pump runs it. It is not `root.after` because `after` from a foreign thread
enters Tcl and **blocks** until the interpreter thread runs the call; close the
window at that moment and the worker is stranded inside Tcl forever, with no
error on any channel (F-12, measured).

**Tk → worker** is two signals: setting `cancel_requested`, which the worker
observes between batches, and `cancel_query()`, which reaches into the pyarrow
reader the worker is blocked in so Stop takes effect at once rather than at the
end of the stream (c6, c7).

**The negative result that matters:** an AST sweep over every worker-reachable
method finds no un-marshalled Tk call. The only widget reads left in that
subgraph are in `_snapshot_export_settings` (1783, 1784, 1788) and
`_resolve_sheet_name` (1810, 1816), both documented Tk-thread-only and enforced
by the `threading.current_thread() is not threading.main_thread()` check at
`app.py:1907`. Everything the export needs is snapshotted into a plain dict on
the Tk thread before the worker starts, so nothing downstream holds a widget
reference.

Shutdown ordering is deliberate (`_on_close`, `app.py:587`): cancel first so the
worker is not inside a multi-second read, then set `shutting_down` before
`destroy()`, so `_ui` starts refusing work rather than queueing it against an
interpreter that is going away.

## 6. Observations that are still true

Defect-level observations from the previous edition are superseded by
[`AUDIT.md`](AUDIT.md)'s 33 `### Status:` blocks. What remains here is
structural — things a diagram or a newcomer needs to know, which are not
findings and were not "fixed".

1. **The package still cannot be imported as a package.** Every submodule uses
   absolute imports (`from constants import ...`), but `__init__.py` uses
   relative ones. Re-verified: `python3 -c "import dremio_Tool"` raises
   `ModuleNotFoundError: No module named 'constants'`, and loading
   `dremio_Tool/utils.py` under the name `dremio_Tool.utils` fails the same way;
   both import cleanly with `dremio_Tool/` on `sys.path`. So `python main.py`
   from inside `dremio_Tool/` remains the only working invocation, and
   `import dremio_Tool`, `python -m …` and the `__init__.py` re-exports are all
   unusable as written. This is why the test harness has `add_src_to_path()`.

2. **Windows-only paths are now guarded, not removed.** `os.startfile` (1031,
   1352) is behind `hasattr(os, 'startfile')` and degrades to a message;
   `root.iconbitmap()` with an `.ico` is still effectively Windows-only;
   `get_ssl_certificate()` still targets the Windows cert store and returns
   `None` elsewhere, which disables verification (reported as a status line).
   Persistence has always been cross-platform.

3. **Four `DEFAULT_CONFIG` keys are still write-only:** `include_timestamp`,
   `apply_table_format`, `table_style`, `auth_method`. They are written to
   `config.json` on every save and never read. `sheet_name` used to be the
   fifth; F-07 made it real, which is what forced the config-version stamp and
   the migration in `config.py:225`.

4. **`utils.py` still prints to stdout** (9 calls, all asset diagnostics), as
   does `connection.py` once (cert extraction). F-20's fix was scoped to the
   persistence layer on purpose — its own table names the six `config.py` calls
   as "the ones that matter" — so this is a recorded boundary, not a regression.

5. **Documentation drift, reduced but not gone.** `dremio_Tool/README.md` is now
   honest about base64 (F-28) and documents the real test suite, but it and
   `main.py`'s docstring still refer to a `dremio_exporter/` directory that does
   not exist on disk. The decimal128 → float64 cast in `connection.py` is still
   undocumented.

6. **The one open finding is open by decision.** F-28's permissions half is
   fixed (`0o600` at creation, `0o700` on the directory); its **encoding** half
   is not — the PAT is base64-obfuscated, not encrypted, and the fallback is
   kept with the README made honest instead. A clean suite run reports it
   CONFIRMED. That is the expected result, not a regression.
