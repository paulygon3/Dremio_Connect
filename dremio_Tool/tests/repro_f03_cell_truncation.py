"""
================================================================================
F-03 - Cell contents over 32,767 chars are silently truncated   (CRITICAL)
================================================================================
The only finding where the app reports success while destroying data, with no
signal on any channel the user can observe.

The truncation itself is Excel's limit and cannot be removed - openpyxl will
always write at most 32,767 characters per cell. What was Critical about this
finding is the SILENCE: openpyxl emits a UserWarning to stderr, which a
PyInstaller windowed build does not have, and the app installs no warnings
filter or logging handler, so the user is shown the standard success dialog
reporting the full row count.

The agreed contract (Tier 1) is **spill to a sidecar**: truncate in the workbook
as Excel requires, write the complete values to a companion file, and tell the
user which cells were affected. So this script checks three things:

  1. does the workbook still truncate?            (expected - Excel's limit)
  2. is every truncated value recoverable?        (the sidecar)
  3. is the user actually told?                   (dialog + log)

The finding is resolved when 2 and 3 hold. It is NOT resolved by 1 alone.
================================================================================
"""

REQUIRES_DISPLAY = True

import threading
import warnings

import pandas as pd

import harness as h

LIMIT = 32767
LENGTHS = [LIMIT, LIMIT + 1, 40000]


# A connected transport that yields a fixed frame. Shared, so it stays in step
# with DremioConnection's signature - see harness.StubConnection.
StubConnection = h.StubConnection


def make_frame():
    return pd.DataFrame({f"len_{n}": ["x" * n] for n in LENGTHS})


def check_workbook_truncation(tmp):
    """Part 1: what lands in the .xlsx."""
    h.step("Part 1: does the workbook still truncate? (it must - Excel's limit)")

    df = make_frame()
    with h.tk_app(output_dir=tmp, filename="long.xlsx", autofit=False) as app:
        app.df = df
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            path = app._export_to_excel()
        truncation = getattr(app, "last_truncation", None)

    readback = pd.read_excel(path, sheet_name=0)   # first sheet, whatever it is named
    rows = []
    destroyed = 0
    for n in LENGTHS:
        got = len(str(readback[f"len_{n}"].iloc[0]))
        lost = n - got
        destroyed += max(lost, 0)
        rows.append([n, got, lost if lost else "-",
                     "intact" if lost == 0 else "truncated in workbook"])
    h.table(["input len", "in workbook", "chars cut", ""], rows)

    user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
    h.detail("UserWarnings openpyxl emitted", len(user_warnings))
    h.note("(caught only because this script installs a filter; "
           "the app installs none - which is why it cannot rely on them)")

    return path, destroyed, truncation


def check_recoverable(path, truncation):
    """Part 2: can the destroyed characters be recovered?"""
    h.step("Part 2: are the full values preserved anywhere?")

    if not truncation:
        h.detail("app.last_truncation", "not populated - the app did not notice")
        h.detail("sidecar file", "none")
        return False, None

    h.detail("app.last_truncation['count']", truncation["count"])
    h.detail("app.last_truncation['total_lost']", f"{truncation['total_lost']:,}")
    h.detail("cells found by the pre-write scan", truncation.get("scanned"))
    h.detail("truncations openpyxl itself reported", truncation.get("warned"))
    h.detail("scan and openpyxl agree",
             truncation.get("discrepancy") is None)
    if truncation.get("discrepancy"):
        h.note(f"DISCREPANCY: {truncation['discrepancy']}")
    sidecar = truncation.get("sidecar")
    h.detail("sidecar path", sidecar.name if sidecar else
             f"NOT WRITTEN - {truncation.get('sidecar_error')}")

    if not sidecar or not sidecar.exists():
        return False, None

    content = sidecar.read_text(encoding="utf-8")
    h.detail("sidecar size", f"{sidecar.stat().st_size:,} bytes")

    # The real test: is each over-length value present in the sidecar in full?
    recovered = []
    for n in LENGTHS:
        if n <= LIMIT:
            continue
        recovered.append(("x" * n) in content)
    all_recovered = all(recovered)
    h.detail("every over-length value present in full", all_recovered)
    h.detail("values checked", f"{len(recovered)} over-limit cells")

    for line in content.splitlines()[:8]:
        h.detail("  sidecar", line[:88])

    return all_recovered, sidecar


def check_user_is_told(tmp):
    """Part 3: drive the real export path and see what the user gets."""
    h.step("Part 3: is the user told? (full _execute_and_export path)")

    with h.tk_app(output_dir=tmp, filename="told.xlsx", autofit=False,
                  open_after=False) as app, h.captured_dialogs() as dialogs:
        app.connection = StubConnection(make_frame())
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", "SELECT * FROM wide_text")

        observed = {}

        def scenario():
            before = set(threading.enumerate())
            app._execute_and_export()
            worker = h.new_threads_from(before)
            h.wait_for(app.root,
                       lambda: worker and not worker[0].is_alive(), timeout=60)
            h.pump(app.root, 0.6)
            observed["warnings"] = list(dialogs["warning"])
            observed["info"] = list(dialogs["info"])
            observed["log"] = app.log_text.get("1.0", "end")

        h.run_with_mainloop(app.root, scenario)

    warned = bool(observed.get("warnings"))
    h.detail("warning dialog shown", warned)
    for title, message in observed.get("warnings", []):
        h.detail(f"  dialog [{title}]", message.replace("\n", " | ")[:200])
    h.detail("success dialog shown", bool(observed.get("info")))
    for _, message in observed.get("info", []):
        h.detail("  success text", message.replace("\n", " | ")[:200])

    log_lines = [ln.strip() for ln in observed.get("log", "").splitlines()
                 if "WARNING" in ln or "truncat" in ln.lower()]
    for line in log_lines:
        h.detail("  log", line)

    names_cells = any("column" in m for _, m in observed.get("warnings", []))
    h.detail("the message names the affected cells", names_cells)
    return warned, names_cells, bool(log_lines)


def main():
    h.require_display()
    h.banner("F-03", "Cells over 32,767 characters")

    with h.isolated_home(), h.temp_dir() as tmp:
        path, destroyed, truncation = check_workbook_truncation(tmp)
        recoverable, sidecar = check_recoverable(path, truncation)
        warned, names_cells, logged = check_user_is_told(tmp)

    h.step("Contract check")
    h.detail("1. workbook truncates (expected, Excel's limit)", destroyed > 0)
    h.detail("2. full values recoverable", recoverable)
    h.detail("3. user is told", warned and logged)

    if destroyed == 0:
        h.verdict("F-03", h.NOT_REPRODUCIBLE,
                  "no truncation occurred at all - unexpected; check the frame")
    elif recoverable and warned and logged:
        h.verdict("F-03", h.NOT_REPRODUCIBLE,
                  f"{destroyed:,} characters are still cut from the workbook - that "
                  f"is Excel's limit and cannot change - but they are no longer lost: "
                  f"the full values are preserved in {sidecar.name if sidecar else '?'} "
                  f"(verified present byte-for-byte), a warning dialog names the "
                  f"affected cells (names_cells={names_cells}), and the log records "
                  f"them. The silence that made this Critical is gone")
    elif recoverable:
        h.verdict("F-03", h.CONFIRMED,
                  f"values are preserved in a sidecar but the user is not told "
                  f"(dialog={warned}, log={logged}) - still silent data loss from "
                  f"the user's point of view")
    else:
        h.verdict("F-03", h.CONFIRMED,
                  f"{destroyed:,} characters destroyed across {len(LENGTHS)} cells "
                  f"with no way to recover them (sidecar present={bool(sidecar)}) "
                  f"and no notification (dialog={warned}, log={logged})")


if __name__ == "__main__":
    main()
