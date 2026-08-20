# Repro suite

Executable reproductions for the findings in
[`docs/architecture/AUDIT.md`](../../docs/architecture/AUDIT.md).

> **Counts below were written at Stage 0 and lagged the suite that grew during
> Stage 1.** They are corrected in place where a wrong number would mislead, and
> the Stage-0 figure is kept beside the correction. As of 2026-08-19 the suite is
> **30 scripts covering all 34 findings**, of which **23 need a display and 7 do
> not**. A clean run is **33 NOT REPRODUCIBLE, 1 CONFIRMED** — the CONFIRMED one
> is F-28's encoding half and is expected; see `AUDIT.md` §Stage 1 outcome.
>
> **F-34** (formula/CSV injection, CWE-1236) was added on 2026-08-19 to bring the
> GUI to parity with the `dremio_excel` skill's A-01 fix: a cell beginning
> `= + - @` is quoted so a spreadsheet keeps it as text. It needs a display.

## Prerequisite: Xvfb

**Xvfb is not part of this repository and does not survive a Codespace rebuild.**
23 of the 30 scripts need a display (18 of 22, when this was written). If the
suite starts reporting `STILL BLOCKED`, this is the first thing to check — it is
not a regression in the app.

```bash
sudo apt-get update && sudo apt-get install -y xvfb
xvfb-run -a python -c "import tkinter; r = tkinter.Tk(); print('Tk OK', r.winfo_screenwidth())"
```

The second line should print `Tk OK 1280`. Without a display the same command
fails with `TclError: no display name and no $DISPLAY environment variable`,
which is the control worth knowing.

This mattered historically: when `AUDIT.md` was written there was no display
server, and that one fact is why nine findings rested on reading control flow
rather than running anything. Every one of them is now reproducible.

Worth recording from this machine, because F-12 turns on it:

```
Xvfb screen           1280x1024
Tcl                   8.6.14
tcl_platform(threaded)  1        <- re-check this on any new machine
```

## Running

```bash
python dremio_Tool/tests/run_all.py
```

That is the whole thing — no wrapper needed. Scripts that need a display are
wrapped in `xvfb-run -a` automatically when `$DISPLAY` is unset.

**Budget about 7 minutes.** Measured 2026-08-19: 402 s wall clock for 29 scripts,
of which `repro_f33` alone is 314 s — 78% of the run, because it exercises every
failure path in both worker threads and each one waits out a real dialog cycle.
(F-34, added later the same day, brings the suite to 30 scripts and adds ~1 s.)
`--only` is the answer when you are iterating on one finding; a full run is for
before and after a change, not for every edit.

> *This said "about 70 seconds" until the figure was measured again.* That was
> true of the Stage-0 suite of 23 scripts and is kept here because it explains
> the number you may see quoted in `AUDIT.md` §Stage 0. The suite has grown to 29
> scripts since, and `repro_f33` got slower as it got more thorough.

```bash
python dremio_Tool/tests/run_all.py --list             # what exists, and what needs a display
python dremio_Tool/tests/run_all.py --only f13,f14     # just these
python dremio_Tool/tests/run_all.py -v                 # full output, not just verdicts
```

To run one script directly, add the wrapper yourself if it needs a display:

```bash
xvfb-run -a python dremio_Tool/tests/repro_f13_stop_button.py
python dremio_Tool/tests/repro_f28_credentials.py       # no display needed
```

A script run without a display it needs exits with `STILL BLOCKED` and the
command to use, rather than a `TclError` traceback.

## Which scripts need `xvfb-run -a`

Every script declares `REQUIRES_DISPLAY` at the top; `run_all.py --list` reads
it — **that is the authority, not this table.** 22 of 29 need one, up from 18 of
22 at Stage 0 — anything that builds a real `DremioExporter`.

| Needs a display | Does not |
|---|---|
| f01/f02, f03, f04, f05, f06, f07, f08, f11, f12, f13, f14, f15, f16, f17, f18, f19, f25, f27, f29, f30, f31, f33, f34 | f09/f10, f21, f22, f24, f26, f28, f32 |

The seven that do not touch only `config.py`, `utils.py`, `connection.py` and
`requirements.txt`.

## What these are, and are not

They are **reproductions**, not assertions. Each prints its observations and
ends with one machine-readable line per finding:

```
VERDICT|F-13|CONFIRMED|is_running is assigned at [...] and read nowhere; ...
```

Status is one of `CONFIRMED`, `NOT REPRODUCIBLE`, or `STILL BLOCKED`. A script
exits non-zero only if it *crashed* — a verdict of any kind is a successful run.
That distinction matters during remediation: when a Stage 1 fix lands, the
corresponding script should flip to `NOT REPRODUCIBLE` on its own, and that is
the signal the fix worked.

**They drive the real source, not a copy of it.** The audit established the
Excel-limit findings by copying the body of `_export_to_excel` and running it
against synthetic frames. That proves the bug but makes a useless regression
test, because a copy keeps reporting CONFIRMED after the original is fixed.
These scripts instead build a real `DremioExporter` on a withdrawn Tk root and
call the real method. That is only possible because a display is now available;
it was not when the audit was written.

## Layout

| File | Role |
|---|---|
| `run_all.py` | Runner. Discovers `repro_*.py`, adds `xvfb-run` where needed, parses verdicts, prints the summary. |
| `harness.py` | Shared plumbing: isolated `$HOME`, the real-app builder, dialog capture, RSS sampling, source inspection. |
| `flightserver.py` | A local Arrow Flight server the app's own `connect()` authenticates against. |
| `repro_f*.py` | One script per finding. |

### `harness.py` — the two things worth knowing

**`isolated_home()`** redirects `$HOME` and `%APPDATA%` to a throwaway
directory. `ConfigManager` resolves its app dir at construction and creates it
immediately, so without this every config and credential test would read and
write the developer's real `~/.dremioexporter/`. It must wrap construction, not
just the assertions.

**`run_with_mainloop()`** is mandatory for anything involving the app's worker
threads, and the reason is not cosmetic. Tkinter permits a non-Tk thread to call
`root.after(...)` only while the main thread is inside `mainloop()` — `_tkinter`
sets a `dispatching` flag there. Servicing the queue with `root.update()` does
**not** set it. A harness built on `update()` alone makes `_execute_thread` die
at its first progress callback with `RuntimeError: main thread is not in main
loop`, which looks like a finding and is really a test artifact. This cost an
hour during Stage 0; it is written down so it costs nobody else one.

### `flightserver.py` — why a local Flight server

`AUDIT.md` tagged F-13 and F-14 SOURCE-only, and the audit expected them to
stay that way because reproducing them needs "a display and a live Dremio
endpoint". Xvfb solved the display. This module solves the rest: an in-process
`FlightServerBase` that speaks enough of Dremio's dialect for the app's real
`DremioConnection.connect()` to succeed unmodified —

- a `ServerAuthHandler`, so `authenticate_basic_token` is implemented at all
- a `ServerMiddleware` returning an `authorization: Bearer ...` response header,
  which is exactly what `DremioClientAuthMiddleware.received_headers`
  (`connection.py:48`) is written to capture

So the tests exercise the real auth middleware, the real bearer-token replay,
the real `get_flight_info` / `do_get` / `read_all` sequence, and the real
`_arrow_to_pandas` cast. `info_delay` widens the window F-14 races against;
`batch_delay` makes the stream long enough to press Stop against for F-13.

**It is not Dremio.** Findings whose mechanism depends on Dremio-specific server
behaviour are not settled by it, and the scripts that rely on the distinction
say so in their verdict text.

Two traps it documents, both of which produced wrong numbers first:

- `connect()` runs `_test_connection`, which performs its own `do_get` and
  `read_all` against the same server. Call `reset_counters()` after connecting
  or the query under test starts with a whole extra stream already counted.
- For F-08, the server runs in a **separate process**. An in-process server
  holds its own full copy of the served table inside the process being measured,
  inflating the baseline and every residency figure by the size of the data.

## Notes on individual scripts

- **f08** is the slow one (~28s): 1,000,000 cells through openpyxl. It measures
  both `ru_maxrss` (the audit's metric, a monotonic high-water mark) and current
  RSS from `/proc/self/statm`, which falls when memory is released and therefore
  measures *concurrent residency* — the thing the audit's table could not
  distinguish. It probes `self_destruct` with `pa.total_allocated_bytes()`, not
  RSS: RSS is the wrong instrument, because CPython and glibc do not return
  freed pages promptly and an RSS-based probe reports "0 MB freed" in every case
  regardless of the truth.

  Its verdict turns on the **export stage alone** — peak growth from after the
  DataFrame exists to after the file is written — not on the whole-path figure.
  Everything before the export is the cost of having the data at all, and
  folding it in would move the number for reasons that have nothing to do with
  this finding. The budget is 100 bytes per cell against ~453 measured before
  the fix and ~37 after, so it does not need to be delicate.

  It also checks two things a memory number cannot see. The export writes the
  rows itself now, so it converts the values itself too: the script writes the
  same 8-dtype frame through the app **and** through `df.to_excel`, and requires
  the two workbooks to read back identical in value *and* type — a datetime
  written as text compares equal to nothing in Excel and sorts as a string. And
  it drives the real Execute handler on the real worker thread to check the
  frame is released, because calling `_export_to_excel` directly leaves
  `app.df` set whatever the code does, and reporting that as "never released"
  would be an artifact of how the test called it.

- **f11** records the exception's *text*, never the exception object. While an
  exception is alive its traceback pins the frame of `_export_to_excel`, which
  pins the abandoned `ExcelWriter`, so its zip central directory is never
  written and the file on disk is a truncated non-zip. Once the handler ends the
  writer is finalised and the file becomes the plausible metadata-only zip the
  user actually finds. `_execute_thread` lets `e` go the same way, so this
  mirrors the app.

- **f28** pairs every permission measurement with a plain-`open()` control file
  in the same directory under the same umask. This container does not honour
  umask, so an uncontrolled measurement means nothing — a fact that invalidated
  an earlier draft of the finding. Any permission test written during
  remediation must keep the control.

- **f33** covers a finding that was not in the original audit. It was found
  while reproducing F-14: the disconnect race fired exactly as predicted, but
  the error dialog the audit says the user sees never appeared. It also measures
  the dialog's **delivery rate** across every failure path, which is what
  established that several other findings' consequence narratives were wrong.

- **f12** measures the regimes in a **subprocess**, and that is load-bearing.
  Regimes B and C deliberately strand daemon threads inside a Tcl interpreter
  that is then torn down; initialising Tk again later in the same process aborts
  it with a C-level `SIGABRT` and no Python traceback. The first draft of this
  script did exactly that to itself and looked like it had found an
  interpreter-level crash in the app. It had not — it had found one in the test.

## Coverage

**Current: 30 scripts cover all 34 findings.** Two scripts carry two findings
each — `repro_f01_f02_column_letters.py` and
`repro_f09_f10_resource_release.py`. Only **F-20** and **F-23** have no script of
their own: F-20 shares its fix and its verification with F-22, and F-23 is
covered inside the validation repros. Every other finding has a dedicated
script.

> *As written at Stage 0, and kept because it records why the gaps existed
> then:* "23 scripts cover 24 findings. Not covered, and why: F-05, F-09, F-10,
> F-18, F-20, F-23, F-26, F-31 and F-32 were already EXECUTED or STATIC in the
> audit and sit in the later remediation tiers. F-05's *dialog* consequence is
> covered inside `repro_f33`, but its `IllegalCharacterError` boundary has no
> dedicated script." — every one of those except F-20 and F-23 gained a script
> during Stage 1, as each fix landed.
