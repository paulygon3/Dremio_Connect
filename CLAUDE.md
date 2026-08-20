# Working in this repository

A Tkinter desktop app that queries Dremio over Arrow Flight and exports to Excel.
All application code is in `dremio_Tool/`. A completed audit-and-fix programme
(Stage 1) was merged into `main` on 2026-08-18 as a fast-forward from
`remediation`, so `main` is the trunk and its history is one commit per finding.
The `remediation` branch is kept as a marker and points at the same commit.

Stage 2 (re-verify structure) was merged into `main` the same way on 2026-08-19,
from `architecture-mapping`, after a full suite run.

**The whole audit-and-fix programme — Stages A, 0, 1 and 2 — is done, and `main`
carries all of it.**

`remediation` and `architecture-mapping` are kept as markers at the commits they
were merged at.

## Read these before starting work

| File | What it is |
|---|---|
| `docs/architecture/AUDIT.md` | 34 findings (33 audit + F-34, a later parity backport). Every one carries a `### Status:` block. **Start here.** |
| `docs/architecture/INVENTORY.md` | Structure, re-derived from source at Stage 2. §4 is the edge classification (import / dead / runtime-only); §5 is the threading model. |
| `dremio_Tool/tests/README.md` | How the reproduction suite works. |

## The three things not to get wrong

**1. A clean test run is `33 NOT REPRODUCIBLE, 1 CONFIRMED`.**

```bash
python dremio_Tool/tests/run_all.py          # ~7 min (402s measured 2026-08-19)
python dremio_Tool/tests/run_all.py --only F-08 -v
```

`repro_f33` is 314s of that 402s, so use `--only` while iterating and keep full
runs for before-and-after. Older prose in `AUDIT.md` and `tests/README.md` quoted
70 seconds; that was the Stage-0 suite of 23 scripts, and both now say so.

> Was `32 NOT REPRODUCIBLE, 1 CONFIRMED` (29 scripts) until **F-34** was added on
> 2026-08-19 — a formula/CSV-injection safeguard (CWE-1236) backported from the
> `dremio_excel` skill's A-01. The suite is now 30 scripts / 34 findings. Dated
> narrative below that still says 32/1 is the record of an earlier run.

The one CONFIRMED is **F-28 (encoding)** — the PAT is stored base64-obfuscated,
not encrypted. That is a decision the user took deliberately: keep the fallback,
make the README honest. It is not a regression and not an oversight. Do not
"fix" it by adding encryption without asking.

**2. Prove a repro fails against the pre-fix code.**

Before committing any fix, stash the source change and re-run — the repro must
flip to CONFIRMED:

```bash
git stash push dremio_Tool/app.py
python dremio_Tool/tests/run_all.py --only F-XX -v
git stash pop
```

This is not ceremony. Two repros silently passed against a broken build before
it became routine: F-22's grepped for the machinery it was checking (and the
fix's own comment named `os.replace`, so the grep matched before the fix
existed), and F-08's had a hardcoded verdict. A repro that cannot tell fixed
from broken launders an assumption as evidence.

Where a measurement could be confounded — timing, memory, file permissions —
include a control in the same run. This container **does not honour umask**, so
any permission test needs a plain-`open()` control file beside it or the result
means nothing.

**3. Record corrections; do not overwrite them.**

Where running the code contradicted the audit's reasoning, both the original
claim and the correction stay on the page (see F-10's leak, F-12's severity,
F-08's copy #1). The same applies to status blocks that turn out to lag their
own fixes.

## Environment

- **Check the branch before working.** `main` is the repository's default, so a
  freshly created Codespace or clone lands there — not on the stage branch. The
  session's startup context reports whichever branch is checked out, so read it
  rather than assuming: stage work belongs on its own branch, never on `main`.
- **Starting from a fresh Codespace or clone.** `.devcontainer/` installs Xvfb
  and the pinned dependencies on create, then verifies the interpreter version,
  headless Tk, and the pins against what is installed — nothing else installs
  any of it. Outside a Codespace, run `bash .devcontainer/setup.sh` once by
  hand. (`.claude/settings.local.json` and the Claude Code
  memory files are machine-local by design and hold nothing the repo does not.)
- **Tk needs a display.** `run_all.py` wraps scripts in `xvfb-run -a` when
  `$DISPLAY` is unset. Xvfb is **not** part of the repo and does not survive a
  Codespace rebuild — if scripts start reporting `STILL BLOCKED`, reinstall it
  (`sudo apt-get install -y xvfb`) before suspecting a regression.
- **Dependencies are pinned exactly** (F-32), and `repro_f32` checks each pin
  against what is installed — so it doubles as a drift detector for every other
  verdict. Change a pin, re-run the suite, confirm the summary is unchanged.

## Conventions

- One commit per finding; clean bisect matters. Commit messages say what was
  wrong, what changed, and what was measured.
- The app's modules use absolute imports (`from constants import ...`), so
  `dremio_Tool/` must be on `sys.path` — it cannot be imported as a package.
  `harness.add_src_to_path()` does this for tests.
- `connection.py`, `config.py` and `utils.py` never import the UI.
  `connection.py` reports through `on_status` callbacks and `config.py` through
  an `on_warning` slot instead — deliberate seams, and ones no import graph will
  show you. `INVENTORY.md` §4 bucket (c) traces all twelve of them.
- **Workers never call into Tcl.** Background work returns to the UI by putting a
  callable on `_ui_queue`, drained on the Tk thread by a 50 ms pump. There is no
  `root.after(0, ...)` anywhere — F-12 removed it, and older prose in
  `AUDIT.md` still describes the mechanism it replaced.
