# Build log — dremio-to-excel skill

A sequenced record of what was built, why, and what was verified. Newest work
appended at the end. Times are approximate; the machine date is 2026-08-19.

Environment (discovered, not assumed):
- Dremio Software (self-managed), `dremio.lid-prod.aws-eu1.energy.local:32010`,
  Arrow Flight over gRPC+TLS, PAT auth, user `UI720086`.
- Python 3.13.1 on Windows 11. keyring → `WinVaultKeyring` (Windows Credential
  Manager). `INFORMATION_SCHEMA` is permission-filtered (`sys.*` denied).
- Global venv drifts from the desktop repo's pins (pandas 2.2.3/pyarrow 22.0.0
  vs pinned 3.0.5/25.0.1) → the skill ships its own isolated, pinned venv.

---

## Step 0 — Approved design decisions

Settled with the user before building:
1. Connect to prod read-only for catalog sizing — **yes**.
2. Install location — **personal-style**, but built inside the project under a
   new folder `dremio_excel_skill/` at the user's request.
3. Dependencies — **isolated pinned venv** for the engine.
4. Decimals — **preserve exact `decimal`** (do not cast to float); matters for
   LMP/price data.

Core constraint (agreed): a fixed, versioned Python engine owns connection,
query execution and export; the agent only gathers params, invokes it, and
reports. Anti-drift is enforced by SKILL.md wording **and** by hard limits +
a mandatory preflight→plan_id→export gate in the engine itself.

Real catalog size (measured live): ~9,004 schemas, 72,273 tables, 1,289,185
columns; a `LIKE` scan takes ~40s → an offline SQLite+FTS5 index is mandatory,
never loaded into context.

---

## Step 1 — Existing tool: add a column limit  (why: parity with row limit)

The desktop tool checked the Excel **row** ceiling before writing but not the
**column** ceiling (16,384 / XFD). openpyxl doesn't enforce it in write_only
mode, so a too-wide frame produced a workbook Excel refuses to open *after*
reporting success — the same failure shape the row check was created to prevent.

Changes:
- `dremio_Tool/constants.py` — added `EXCEL_MAX_COLUMNS = 16384` with an
  explanatory comment matching the row-limit style.
- `dremio_Tool/app.py` — added `_check_column_ceiling()` mirroring
  `_check_row_ceiling()`, wired into `_export_to_excel()` immediately after the
  row check (before anything touches disk).

Verified: both files compile; `constants.EXCEL_MAX_COLUMNS == 16384`; no
lint/type errors.

---

## Step 2 — Skill scaffold + engine  (folder: `dremio_excel_skill/`)

Layout:
```
dremio_excel_skill/
  SKILL.md                     # frontmatter + golden rule + workflow
  requirements.txt             # pinned: pandas 2.2.3, pyarrow 22.0.0, openpyxl 3.1.5, numpy 2.2.0, keyring 25.7.0
  reference/{credentials,catalog,export-limits,troubleshooting}.md
  dremio_excel/
    __init__.py __main__.py VERSION
    limits.py       # caps = Excel max (1,048,575 rows / 16,384 cols); timeout 300s; 2GB
    result.py       # typed EngineError + JSON contract + exit codes + stderr progress
    paths.py        # app-data locations (out of context, survive skill updates)
    connection.py   # Arrow Flight + auth; middleware fix; cap enforcement in read loop
    credentials.py  # named sets: PAT in keyring, metadata in credsets.json
    catalog.py      # SQLite+FTS5 index; Prod/Gold ranking; per credential set
    preflight.py    # cheap estimate (LIMIT sample + bounded COUNT) + plan_id gate
    export.py       # headless writer; row+column caps; truncation sidecar; atomic; csv/parquet
```

Why key choices were made:
- **Middleware fix**: the desktop auth middleware raised "Did not receive
  authorization header back from server" on every RPC after the handshake
  (harmless — cached bearer token still works — but floods stderr). The engine
  captures only on the handshake and stays silent afterward.
- **TLS fails closed**: if the RWE CA isn't found, raise `NETWORK` instead of
  silently disabling verification (opt out only via `--allow-unverified-tls`).
- **No base64 credential fallback**: if no keyring backend exists, `creds add`
  refuses rather than writing a reversible file.
- **Caps = Excel max**, enforced in the engine, overridable only by explicit
  flags. Over the caps, xlsx is refused and Parquet recommended.
- **Runaway prevention**: hard row/byte/time caps checked in the streaming read
  loop; single-flight lock; one query per invocation; mandatory
  preflight→plan_id→export gate so preflight can't be skipped.
- **Secrets**: `creds add` reads the PAT from `DREMIO_PAT` or an interactive
  prompt — never argv, never a chat message.

---

## Step 3 — Offline verification  (why: prove the engine before going live)

Smoke tests (global venv), all PASS:
- `version` → ok, keyring `WinVaultKeyring`.
- `export` with bogus `plan_id` → `PLAN_MISMATCH` (exit 42) **before** any
  network call — proves the gate.
- xlsx write with **decimals preserved**; row ceiling; column ceiling; csv;
  parquet; SQLite FTS5 available.
- Caps confirmed: 1,048,575 rows / 16,384 cols.

---

## Step 4 — Live end-to-end (complete)

- Created isolated venv `dremio_excel_skill/.venv` with pinned deps. ✅
- Seeded credential set `prod-ro` by reusing the existing desktop keyring token
  **in-process** (never printed or passed as an argument). ✅
- `catalog refresh --credset prod-ro`: swept **1,289,464** column rows in
  **227.2s** and indexed **72,291 tables** into
  `%APPDATA%\DremioExporter\catalog\catalog_prod-ro.sqlite`. ✅
- `catalog search --term "MISO real time LMP"`: ranked the **Prod/Gold** tables
  to the top (penalty −20), top hit
  `Core.Preparation.S3.Team_US.Entropy.Prod.Data.MISO.Gold.miso_lmp_real_time_5_min`
  (cols incl. `lmp`, `energy`, `congestion`). ✅
- `preflight` on a bounded pull → tier green, xlsx feasible, `plan_id` minted. ✅
  (That Gold table currently returns **0 rows** to this role — likely populated
  by a pipeline and empty now.)
- `export` with the matching `plan_id` → wrote
  `Documents\Dremio_Exports\miso_lmp_demo.xlsx` (15 columns, 0 data rows),
  atomic write, plan gate honoured. ✅

**The full connect → search → preflight → plan_id → export chain is proven live.**

### Follow-ups
- Catalog sweep messaging corrected from "~40s" to "a few minutes" in
  `catalog.py` and `reference/catalog.md`. **[DONE]**
- Consider a bounded/streamed sweep or server-side pagination if refresh time
  becomes a problem.
- For a non-empty demo, target a table known to hold data (a fresh `preflight`
  is required per query, since `plan_id` binds to the exact query text).

---

## Step 5 — Evaluated an external skill brief (SKILL_BRIEF.md)

A brief from another chat was reviewed. It was written in a **different, broken
container** (Linux Codespace, missing pins, no Dremio reachable), so several of
its "blocking" unknowns are already resolved live here (host, catalog size,
cert, that unquoted `INFORMATION_SCHEMA.COLUMNS` works).

Adopted from it:
- **Read-only SQL guard** — `_ensure_read_only()` in `__main__.py` refuses
  anything but SELECT/WITH/SHOW/DESCRIBE/EXPLAIN (and rejects multi-statement)
  unless `--allow-write` (off by default). Verified: `DELETE` → `SQL_ERROR`,
  exit 12, before any network. **[DONE]**

Noted for decision / later (not yet done):
- **Shared module vs standalone**: the brief assumes the GUI is refactored to
  consume one canonical module (extract-with-wrappers, re-verify the 33 repros).
  This build is instead a **standalone engine** that duplicates the export
  logic. This is the one real strategic fork — needs a user decision.
- **Preflight file-size by real 1,000-row probe** through the writer (measure,
  not model) — more accurate for zip-compressed xlsx; also surfaces truncation
  early.
- **Parquet straight from the Arrow table** (skip pandas amplification).
- **Startup pin check** (repro_f32-style) to refuse on dependency drift.
- **aliases.yaml** is documented but not yet wired into `catalog.search`.
- **Provenance**: stamp engine version + run-id into workbook properties and a
  `.manifest.json` sidecar (anti-drift traceability).
- **Credentials**: add a `secret_backend: "none"` tier; consider file-per-set.

---

## Step 6 — Adopted brief improvements (user: standalone, adopt all six)

All six implemented and verified:
- **Read-only SQL guard** — `_ensure_read_only()` rejects non-read-only and
  multi-statement SQL unless `--allow-write` (off by default). Live `DELETE` →
  `SQL_ERROR`, exit 12, before any network. ✅
- **Credentials `none` tier** — `creds add --no-store` persists nothing
  (`secret_backend: "none"`); run reads `DREMIO_PAT`. Backend announced every
  connect. ✅
- **Startup pin check** — `checkenv.enforce_pins()` before every data command;
  clean in the isolated venv. ✅
- **Parquet straight from Arrow** — `export_arrow_parquet()`; decimals stay
  exact (`object`, not `float64`). ✅
- **Provenance** — engine + run-id stamped into workbook properties / parquet
  metadata, plus a `<file>.manifest.json` sidecar. ✅
- **Real 1,000-row probe** — preflight measures file size through the actual
  writer and surfaces truncation early. ✅
- **aliases.json resolution** — `catalog search` consults aliases first
  (`source: "alias"`); `catalog alias`/`aliases` manage them. Live: "miso rt
  lmp" → Gold 5-min table first. ✅

Open fork left by choice: **standalone**, so the GUI keeps its own copy of the
export logic (no shared-module extraction).

---

## Step 7 — GUI backports, richer logging, log retention + full Windows repro run

### GUI changes (dremio_Tool/), all verified live
- **Column limit**: `EXCEL_MAX_COLUMNS = 16384` + `_check_column_ceiling()` before
  the write (mirrors the row ceiling).
- **Auth-middleware spam fixed**: `received_headers` no longer raises on later
  RPCs (Dremio does not re-echo the header; the cached bearer token still
  authenticates). Verified 0 stderr bytes on a live connect+query.
- **TLS**: the silent verification-disable on a missing CA is now a loud
  `WARNING` (still connects; only the signal is louder).
- **Richer activity logging**: middleware spin-up, per-RPC `FlightMethod` status,
  bearer-token capture, and connect/execute timing.
- **Per-session .txt log** at
  `%APPDATA%\DremioExporter\logs\dremio_log_<YYYYMMDD_HHMMSS>.txt`, mirroring
  every panel line + session start/end. Verified created and populated.
- **Saved GUI setting "Keep logs (days)"** (`config: logging.log_retention_days`,
  default 30, `0` = forever). Old logs pruned on startup. Verified: a 40-day log
  was pruned, a 1-day log kept, and the setting round-trips through save/load.
- **Decimal**: left as `decimal128 -> float64` in the GUI (deliberate — Excel is
  float64 internally, so preserving Decimal for an xlsx-only tool is a no-op with
  added risk; the skill's Parquet path is where exact decimals matter).

### Full repro suite run — Windows, 2026-08-19
Run on **Windows 11 + pandas 2.2.3** (`$DISPLAY` set so native Tk is used).
**This is NOT the canonical gate** — the `32 NOT REPRODUCIBLE, 1 CONFIRMED`
baseline is Linux + pinned pandas 3.0.5; run the official gate in the
devcontainer. Result here: **21 NOT REPRODUCIBLE, 3 CONFIRMED, 2 STILL BLOCKED,
5 did-not-complete, in 409s.**

- **NOT REPRODUCIBLE (21)** — fixes hold: F-03, F-04, F-06, F-07, F-09, F-10,
  F-11, F-12, F-13, F-14, F-15, F-19, F-21, F-22, F-25, F-26, F-27, F-29, F-30,
  F-31, F-33. Every finding tied to code changed in Steps 1/6/7 passed.
- **CONFIRMED (3), all explained**:
  - F-28 (encoding) — the deliberate base64 fallback (canonical "1 CONFIRMED").
  - F-28 (perms) — Windows artifact: no POSIX `0o600` enforcement, control file
    matches, umask not honoured.
  - F-32 — dependency-drift detector firing correctly (pandas 2.2.3 vs pinned
    3.0.5, etc.); confirms this env is not the verified pinned one.
- **STILL BLOCKED (2)**: F-16, F-17 — `os.startfile` exists on Windows, so the
  non-Windows failure path can't be exercised here.
- **Did not complete (5), platform artifacts, not code defects**:
  - F-01/F-02, F-05, F-24 — `UnicodeEncodeError` printing Unicode/emoji to the
    cp1252 Windows console.
  - F-08 — `ModuleNotFoundError: resource` (Unix-only).
  - F-18 — `AttributeError: os.geteuid` (Unix-only).

**Conclusion**: my changes are repro-safe; the deviations from the canonical
number are entirely platform (Windows vs Linux) and environment (pandas 2.2.3 vs
3.0.5). Re-run the official gate in the Linux devcontainer for the record number.

---

## Step 8 — App/skill parity audit + engine security pass (2026-08-19)

Goal: confirm the GUI and the engine are identical in every *meaningful*
behaviour except the intended forks, then hunt the engine for security/failure
gaps and prove each with a repro. All engine-only; no GUI source was touched.

### 8a — Parity audit (app vs engine)

Compared the shared logic line by line. **Identical**: routing tag
(`lid-toolbox-default-tag`) and CA name (`RWE Server Auth Issuing CA`), auth
middleware (capture-then-silent), `get_ssl_certificate`, Excel limits
(1,048,576 sheet / 1,048,575 data / 16,384 cols / 32,767 chars), `_excel_value`
coercion, illegal-char sanitising, over-length sidecar, atomic temp→`os.replace`,
streaming read/discard-on-cancel, version 3.0.0.

**Intended forks (left as-is, documented in the engine):** TLS fail-closed vs
warn-and-downgrade; keyring-only vs base64 fallback; `Decimal` preserved vs
`decimal128→float64`; headless JSON CLI (preflight→plan_id gate, catalog,
single-flight, read-only) vs GUI.

### 8b — Findings, fixes and repros (all in `dremio_excel/`)

A headless repro suite was added under `dremio_excel_skill/tests/`
(`run_all.py` + `repro_s0*.py`, no display needed). Clean run: **6 NOT
REPRODUCIBLE, 0 CONFIRMED**. Details in [`tests/README.md`](tests/README.md).

| ID | Finding | Fix | Proof |
|----|---------|-----|-------|
| **S-05** | `export.py` defined the Excel sheet-name rules in `limits.py` but never enforced them, so `--sheet-name` could write a workbook Excel repairs/refuses after reporting success (the desktop tool's F-07, unported). | Added `export.validate_sheet_name()` using those constants; wired into the xlsx write path and re-checked early in `cmd_export`. | `repro_s05`; verified it CONFIRMS with no validation. |
| **S-01** | `single_flight()` judged a peer lock stale after a hardcoded `DEFAULT_TIMEOUT_S + 60` (360 s), ignoring the run's `--timeout`. A legit `export --timeout 1800` was stealable after ~6 min → two concurrent heavy queries on Dremio, defeating the single-flight guarantee. | The lock now records `expires = started + ttl`; a peer is active until its own expiry passes (legacy locks fall back to the old window). `export` passes `ttl = timeout + 60`. | `repro_s01`; **run against the reverted predicate → CONFIRMED**, restored → NOT REPRODUCIBLE, with stale/recent controls. |
| **S-06** | The engine wrote `--format` bytes regardless of the `--out` name, so `--format xlsx --out data.csv` produced a file that lied about its contents. | Added `_validate_out_extension()`; a present extension contradicting `--format` is refused (`USAGE`) before any network work; a missing extension is allowed. | `repro_s06`. |

### 8c — Safeguards audited and confirmed sound (regression repros added)

- **S-02 read-only SQL guard** — writes/multi-statements → `SQL_ERROR` before any
  network; `--allow-write` overrides. (Known conservative edge: a `;` inside a
  string literal is a safe fail-closed false-reject — documented, not "fixed"
  with a fragile SQL parser.)
- **S-03 TLS fail-closed** — no CA + verified TLS → `NETWORK` before a client is
  built; `--allow-unverified-tls` connects and announces the downgrade.
- **S-04 no keyring** — storing a PAT is refused (`INTERNAL`) with **no** secret
  written to disk; the `none` tier persists nothing.

Also checked and found safe: PAT never in argv and scrubbed post-auth; catalog
FTS tokenised to alphanumerics (no injection); credential-set slug strips path
traversal; `checkenv.enforce_pins()` gates data commands.

### Files changed / added

- `dremio_excel/export.py` — `validate_sheet_name()` + xlsx-path call.
- `dremio_excel/__main__.py` — timeout-aware `single_flight(ttl=…)`; early
  sheet-name check; `_validate_out_extension()` + call; `export` passes its ttl.
- `dremio_excel_skill/tests/` — new headless repro suite (`run_all.py`,
  `repro_s01`–`repro_s06`, `README.md`).

No `dremio_Tool/` (GUI) source changed in this step.

---

## Step 9 — Adversarial review + PAT-expiry early warning (2026-08-19)

### 9a — Adversarial review (report-only, no source changed)

An external brief drove a security/failure pass. Findings were **reported, not
fixed** (per the brief's ground rules), with three proven by demonstration tests
under a **separate** folder `tests_adversarial/` (they currently report
`VULNERABLE`, as intended for an open finding):

| ID | Sev | Where | Finding | Proof |
|----|-----|-------|---------|-------|
| A-01 | High | `export._excel_value` | Formula/CSV injection — a value starting `=` becomes a live Excel formula (`+ - @` also unsafe in CSV); no neutralisation. | `proof_a01` |
| A-02 | Medium | `paths.catalog_db`/`_slug` | Two cred-set names differing only by a non-`[A-Za-z0-9_.-]` char share one catalog cache → cross-set disclosure. | `proof_a02` |
| A-03 | Medium | `credentials.delete_set` | Delete removes registry+keyring but leaves the per-set catalog index on disk (data remanence). | `proof_a03` |
| A-04 | High (arch) | `catalog.search` → agent | Harvested table/column names enter agent context undelimited → prompt-injection surface. | static |
| A-05 | Medium | `cmd_export` `--out` | Output path unconfined (traversal/absolute); worse combined with A-04. | static |
| A-06 | Medium | `__main__` `to_pandas()` | `--max-bytes` caps Arrow only; xlsx path then materialises Arrow + a pandas copy → OOM below the "2 GB" impression. | static |
| A-07 | Low/Med | `catalog.search` vs `stats` | Freshness advisory only; `CATALOG_STALE` (44) defined but unused. | static |
| A-08 | Low/Med | `cmd_catalog_search` | Search takes no single-flight lock; racing a refresh (unlink+rebuild) can read a half-built db. | static |
| A-09 | Low | `export` sidecars | `.truncated.txt`/manifest written non-atomically after the xlsx. | static |
| A-10 | Low | `cmd_export` | run_id/timestamp make output non-byte-reproducible (content is stable). | static |
| A-11 | Low (arch) | plan gate | Human approval of preflight is unenforceable; the gate proves a preflight ran, not that a person approved it. | static |

Top-3 recommended first fixes: **A-01**, **A-04+A-05** (the injection→arbitrary-
write chain), **A-02+A-03** (per-set isolation the design promises). Not tested:
anything live against Dremio (no throwaway test credset), end-to-end row
boundaries, and the GUI suite. Architecture concerns: two copies of the export
logic (every fix lands twice), the engine trusts the agent as privileged, and
catalog identifiers are treated as trusted strings.

### 9b — PAT-expiry early warning (implemented, S-07)

Acting on the "auth expires mid-run" failure mode: rather than discover a lapsed
PAT *after* a long fetch, `creds add` now records an **optional, non-secret**
expiry date and the engine warns before the query runs.

- `credentials.add_set(..., pat_expires=)` validates `YYYY-MM-DD`
  (`_normalize_expiry`) and stores it in `credsets.json`; `expiry_status()`
  classifies `expired` / `expiring_soon` (≤14 days). Surfaced by `get_set` and
  `creds list`.
- `__main__`: `creds add --pat-expires`; when interactive and not supplied, the
  engine **prompts for it, explaining why** (a PAT lapsing mid-export fails after
  the query). `_connect` emits a `NOTE` (soon) or `WARNING` (expired) on connect,
  before any query. Prompt/explanation go to stderr; stdout stays one JSON object.
- Docs: `SKILL.md` §Secrets and `reference/credentials.md` tell the agent to ask
  the user for the expiry and why.
- Repro `tests/repro_s07_pat_expiry.py` (capture/validate/surface/classify, no
  secret written). Suite now **7 NOT REPRODUCIBLE, 0 CONFIRMED**. Verified live
  via the CLI (`--pat-expires 2026-01-01` → `expired: true, days_left: -230`);
  the demo set was deleted afterward.

### Files changed / added (Step 9)

- `dremio_excel/credentials.py` — `_normalize_expiry`, `expiry_status`,
  `add_set(pat_expires=)`, expiry in `get_set`/`list_sets`.
- `dremio_excel/__main__.py` — `--pat-expires`, interactive prompt with the
  reason, connect-time expiry warning.
- `SKILL.md`, `reference/credentials.md` — ask-the-user guidance.
- `tests/repro_s07_pat_expiry.py`; `tests_adversarial/proof_a01–a03` (review).

No `dremio_Tool/` (GUI) source changed.

---

## Step 10 — Fixed the top adversarial findings (2026-08-19)

Acted on the Step 9 review. Each fix has a pass/fail regression test in the
**separate** `tests_adversarial/` folder (its own `run_all.py`), which flips the
finding from CONFIRMED to NOT REPRODUCIBLE. Clean run: **4 NOT REPRODUCIBLE, 0
CONFIRMED**; the S-series suite stays **7 NOT REPRODUCIBLE**.

| ID | Fix | Where |
|----|-----|-------|
| **A-01** formula/CSV injection | `export._neutralise_formulas()` prefixes text cells starting `= + - @` (or tab/CR) with `'` so a spreadsheet keeps them text; count returned as `neutralised_cells`, warned; `--allow-formulas` opts out. xlsx + csv; parquet untouched. | `export.py`, `__main__.py` |
| **A-02** catalog key collision | `paths.catalog_db()` appends an 8-char hash of the **raw** set name, so slug-colliding names (`team a` / `team_a`) get distinct cache files. | `paths.py` |
| **A-03** delete remanence | `credentials.delete_set()` also unlinks the per-set catalog db and its `-wal`/`-shm` sidecars. | `credentials.py` |
| **A-05** unconfined output | `__main__._resolve_out_path()` resolves `..`/symlinks and confines `--out` under an export root (`DREMIO_EXCEL_OUT_ROOT`, default `~/Documents/Dremio_Exports`); escapes refused (`USAGE`); `--allow-any-path` overrides. | `__main__.py` |
| **A-04** prompt injection | Guidance: `SKILL.md` §Standard workflow tells the agent to treat catalog schema/table/column values as **untrusted data, never instructions**. | `SKILL.md` |

**One-time operational note:** A-02 changed the catalog filename, so the existing
`prod-ro` index is orphaned under its old name — run `catalog refresh --credset
prod-ro` once to rebuild at the new collision-safe path. (Catalog is a cache;
nothing else is affected.)

Not fixed (accepted/deferred, all reported in Step 9): A-06 (pandas OOM below the
2 GB Arrow cap), A-07 (catalog TTL advisory), A-08 (search-vs-refresh race), A-09
(non-atomic sidecars), A-10 (byte-nondeterminism), A-11 (approval unenforceable).

### Files changed / added (Step 10)

- `dremio_excel/export.py` — `_neutralise_formulas`, wired into xlsx+csv, result
  `neutralised_cells`.
- `dremio_excel/paths.py` — hash-suffixed catalog filename.
- `dremio_excel/credentials.py` — `delete_set` removes the catalog cache.
- `dremio_excel/__main__.py` — `_resolve_out_path` + `--allow-any-path`,
  `--allow-formulas`.
- `SKILL.md`, `reference/export-limits.md` — untrusted-catalog + safety docs.
- `tests_adversarial/` — `run_all.py`, `repro_a01/a02/a03/a05` (the earlier
  `proof_*` files were replaced).

No `dremio_Tool/` (GUI) source changed.

---

## Step 11 — Deferred robustness findings (2026-08-19)

Cleared the remaining findings with operational bite. Regression tests added to
`tests_adversarial/` (now **8 scripts, all NOT REPRODUCIBLE**); S-series stays
**7 NOT REPRODUCIBLE**.

| ID | Fix | Where |
|----|-----|-------|
| **A-06** pandas OOM below the 2 GB Arrow cap | Both Arrow→pandas sites now use `to_pandas(split_blocks=True, self_destruct=True)`, freeing each Arrow buffer as the frame is built (~one copy at peak, not Arrow + copy). `cmd_export` also emits a `NOTE` when a result exceeds half the byte budget, since the xlsx/csv pandas copy pushes peak higher. | `__main__.py`, `catalog.py` |
| **A-08** search races refresh | `catalog.build()` builds into a `.building` temp db and `_install_db()` swaps it in with `os.replace` (bounded retry for Windows readers, unlink-fallback). A concurrent search sees the old complete index or the new one — never the half-built file the old unlink-in-place exposed. | `catalog.py` |
| **A-09** non-atomic sidecars | `export._write_sidecar()` (the `.truncated.txt`) and `__main__._write_manifest()` now write via temp→replace, so a crash never leaves a partial sidecar beside a complete data file. | `export.py`, `__main__.py` |
| **A-07** stale catalog served silently | `cmd_catalog_search` adds a `warnings` entry advising `catalog refresh` when the index is past the 7-day freshness window (`stats.stale`). | `__main__.py` |

Repros: `repro_a06_memory_self_destruct` (typed round-trip + both call sites use
self_destruct), `repro_a07_stale_catalog_warning` (minimal old index → warning),
`repro_a08_atomic_catalog_rebuild` (`_install_db` swap + sidecar cleanup),
`repro_a09_atomic_sidecar` (full value preserved, no `.tmp` left).

**Still open by decision:** A-10 (run_id/timestamp make output non-byte-identical;
content is stable — would add `--deterministic` only if asked) and A-11 (human
approval of preflight is unenforceable by the engine; architectural).

### Files changed / added (Step 11)

- `dremio_excel/catalog.py` — `_install_db` atomic swap, temp-file build,
  `self_destruct` sweep, `os` import.
- `dremio_excel/export.py` — atomic `_write_sidecar`.
- `dremio_excel/__main__.py` — `self_destruct` + large-result note, atomic
  manifest, stale-catalog search warning.
- `reference/catalog.md` — freshness/refresh note.
- `tests_adversarial/` — `repro_a06/a07/a08/a09`.

No `dremio_Tool/` (GUI) source changed.

---

## Step 12 — Deterministic export (A-10) (2026-08-19)

Closed the last actionable review finding. `export --deterministic` makes the
same query produce **byte-identical** output across runs:

- **run_id omitted** (no uuid stamped into xlsx description, parquet metadata, or
  the manifest; `completed_at` becomes null).
- **xlsx timestamps fixed** — workbook `created`/`modified` set to a constant,
  and `_normalize_zip()` rewrites the xlsx (a zip) with a fixed member date-time,
  since openpyxl otherwise stamps each member with the wall clock.
- Engine version (constant per release) is kept for provenance.

Default behaviour is unchanged (run-stamped). Proven by `repro_a10`: two
deterministic exports of the same frame are SHA-256 identical for **xlsx, csv and
parquet**, while a default xlsx pair differs. Suites: **adversarial 9/9, S 7/7,
0 CONFIRMED.**

**A-11 is closed as accepted (won't-fix).** The user decided (2026-08-19) that
autonomous exports are wanted, so an out-of-band human-approval token (a
confirmation code / TTY gate) will **not** be added. The `plan_id` gate stays
(it proves a preflight ran for the exact query/format/caps); the "human approved
the estimate" step remains SKILL.md guidance by design. **No review findings are
now open.**

### Files changed / added (Step 12)

- `dremio_excel/export.py` — `_normalize_zip`, fixed xlsx timestamps,
  `deterministic=` on `export`/`_write_xlsx`/`export_arrow_parquet`.
- `dremio_excel/__main__.py` — `--deterministic`; run_id/`completed_at` suppressed
  when set.
- `reference/export-limits.md` — documents `--deterministic`.
- `tests_adversarial/repro_a10_deterministic_export.py`.

No `dremio_Tool/` (GUI) source changed.

---

## Step 13 — Backport skill safeguards to the GUI for parity (2026-08-19)

A parity review found two safeguards that existed only in the skill's export path
and left the **GUI** (`dremio_Tool/`) behind on genuinely shared behaviour. Both
were backported so the two exporters behave identically; the other divergences
(TLS fail-closed, keyring-only, `Decimal` vs float64, catalog/preflight/CLI
concepts) remain intended.

| Gap | GUI fix |
|-----|---------|
| **Formula/CSV injection (A-01 → F-34)** — the GUI wrote cells verbatim, so a value like `=HYPERLINK(...)` from another Dremio user ran on whoever opened the workbook. | `app._neutralise_formula_cells()` quotes any text cell beginning `= + - @` (or leading TAB/CR) with `'`, reports the affected cells in the log + a dialog (the F-05 replace-and-say contract), and leaves real numbers untouched. Constants `FORMULA_TRIGGER_CHARS`/`FORMULA_PREFIX` added. |
| **Non-atomic truncation sidecar (A-09)** — `.truncated.txt` was written with a plain `open()`. | `_write_truncation_sidecar` now writes temp→`os.replace`, matching the workbook's no-partial-file contract (F-11) and the skill. |

New GUI repro `dremio_Tool/tests/repro_f34_formula_injection.py` drives the real
`_export_to_excel` and reads the workbook back: **NOT REPRODUCIBLE** with the fix,
and verified **CONFIRMED** against the reverted code (live `=1+1`/`=HYPERLINK`).
F-03 (sidecar), F-04, F-05, F-07 re-run clean; the two console
`UnicodeEncodeError` crashes on Windows are the documented cp1252 print artifact,
not code (F-05 passes under `PYTHONIOENCODING=utf-8`).

**Canonical count shift:** the GUI suite is now **30 scripts / 34 findings**, and
a clean run is **33 NOT REPRODUCIBLE, 1 CONFIRMED** (was 32/1). `dremio_Tool/tests/README.md`
is updated. Older dated narrative that quotes 32/1 (`CLAUDE.md`, `AUDIT.md`)
still predates F-34 — propagate the new number there if/when those are next
revised.

### Files changed / added (Step 13)

- `dremio_Tool/app.py` — `_neutralise_formula_cells`, `_neutralised_report_lines`,
  export integration + user reporting, atomic sidecar, `last_neutralised` state.
- `dremio_Tool/constants.py` — `FORMULA_TRIGGER_CHARS`, `FORMULA_PREFIX`.
- `dremio_Tool/tests/repro_f34_formula_injection.py`; `tests/README.md` counts.

This is the one step that **does** change `dremio_Tool/` (GUI) source — by design,
to close a real security gap the skill had already fixed.
