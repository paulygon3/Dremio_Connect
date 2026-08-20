"""
================================================================================
F-16 - On non-Windows, every successful export is reported as an error   (High)
================================================================================
Tagged EXECUTED + SOURCE: the platform fact was measured, the consequence -
that the *placement* of the call inside the try block converts a cosmetic
platform gap into a false failure report - was reasoned. This runs the whole
path against a real Flight server so the consequence is observed rather than
predicted.

    if self.open_after.get():          app.py:726
        os.startfile(filepath)         app.py:727

sits INSIDE the try at app.py:708, after the export has fully succeeded and
before the success messagebox at app.py:729. So on Linux/macOS:

    1. query runs, file is written correctly
    2. os.startfile raises AttributeError
    3. jump to except -> log ERROR, progress label "Error", error dialog
    4. the success dialog is never reached

"Open after export" is checked by default (app.py:297), so this is every export
on any non-Windows machine.
================================================================================
"""

REQUIRES_DISPLAY = True

import os
import threading

import pyarrow as pa

import harness as h


def main():
    h.require_display()
    h.banner("F-16", "Successful exports reported as errors on non-Windows")

    from flightserver import ReproFlightServer, connected_connection

    h.step("Platform fact")
    has_startfile = hasattr(os, "startfile")
    h.detail("os.name", os.name)
    h.detail("hasattr(os, 'startfile')", has_startfile)
    if not has_startfile:
        try:
            os.startfile("/tmp")
        except AttributeError as e:
            h.detail("calling it raises", f"AttributeError: {e}")

    h.step("Where the call sits relative to the success dialog")
    for name, lineno, line in h.grep_source(
            r"os\.startfile|messagebox\.showinfo|except Exception as e|try:",
            ["app.py"]):
        if 700 <= lineno <= 745:
            h.detail(f"{name}:{lineno}", line)

    table = pa.table({"id": pa.array(range(200)),
                      "name": pa.array([f"r{i}" for i in range(200)])})
    server = ReproFlightServer(table=table).start()

    try:
        with h.isolated_home(), h.temp_dir() as tmp:
            # open_after=True is the app's own default (app.py:297).
            with h.tk_app(output_dir=tmp, filename="success.xlsx",
                          autofit=True, open_after=True) as app, \
                    h.captured_dialogs() as dialogs:

                observed = {}

                def scenario():
                    h.step("Running a real query + export with 'Open after export' on")
                    with h.silence_fd_stderr():
                        app.connection = connected_connection(server)
                    server.reset_counters()

                    app.query_text.delete("1.0", "end")
                    app.query_text.insert("1.0", "SELECT * FROM t")

                    before = set(threading.enumerate())
                    app._execute_and_export()
                    worker = h.new_threads_from(before)
                    h.wait_for(app.root,
                               lambda: worker and not worker[0].is_alive(),
                               timeout=60)
                    h.pump(app.root, 1.0)

                    exported = list(tmp.glob("*.xlsx"))
                    observed["file_written"] = bool(exported)
                    observed["rows"] = len(app.df) if app.df is not None else None
                    observed["progress"] = app.progress_label["text"]
                    observed["log"] = app.log_text.get("1.0", "end")
                    observed["info"] = list(dialogs["info"])
                    observed["error"] = list(dialogs["error"])

                    h.step("What actually happened")
                    h.detail("file written correctly",
                             f"{observed['file_written']} "
                             f"({exported[0].name if exported else '-'}, "
                             f"{exported[0].stat().st_size if exported else 0:,} bytes)")
                    h.detail("rows retrieved", observed["rows"])
                    h.detail("progress label now reads",
                             repr(observed["progress"]))
                    h.detail("SUCCESS dialog shown", bool(observed["info"]))
                    h.detail("ERROR dialog shown", bool(observed["error"]))
                    for title, message in observed["error"]:
                        h.detail(f"  error dialog [{title}]", message)
                    for line in observed["log"].splitlines():
                        if any(k in line for k in ("ERROR", "Done", "Note:")):
                            h.detail("  log", line.strip())
                    observed["explained"] = "Note:" in observed["log"]
                    h.detail("user is told why the file did not open",
                             observed["explained"])

                h.run_with_mainloop(app.root, scenario)
    finally:
        server.stop()

    file_written = observed.get("file_written")
    success_shown = bool(observed.get("info"))
    error_evidence = (bool(observed.get("error"))
                      or "startfile" in observed.get("log", ""))
    progress = observed.get("progress", "")

    if has_startfile:
        h.verdict("F-16", h.BLOCKED,
                  "os.startfile exists on this platform (Windows), so the "
                  "non-Windows failure path cannot be exercised here")
    elif file_written and not success_shown and error_evidence:
        dialog_state = ("an error dialog" if observed.get("error") else
                        "NO dialog at all - the error dialog is itself lost to F-33")
        h.verdict("F-16", h.CONFIRMED,
                  f"the export wrote {observed['rows']} rows to disk correctly, then "
                  f"os.startfile raised AttributeError from inside the try block: the "
                  f"success dialog was never reached, the progress label reads "
                  f"{progress!r}, and the user gets {dialog_state}. A completed export "
                  f"is presented as a failure")
    elif file_written and success_shown:
        h.verdict("F-16", h.NOT_REPRODUCIBLE,
                  f"the export succeeded, the success dialog was shown, and the "
                  f"unsupported 'Open after export' is reported as a note rather "
                  f"than an error (explained={observed.get('explained')})")
    else:
        h.verdict("F-16", h.NOT_REPRODUCIBLE,
                  f"unexpected outcome: file_written={file_written} "
                  f"success={success_shown} error={error_evidence}")


if __name__ == "__main__":
    main()
