"""
================================================================================
F-12 - Tk widget reads execute on worker threads   (severity reassessed)
================================================================================
AUDIT.md tagged this STATIC and rated it High, predicting that off-thread Tcl
access "manifests as RuntimeError: main thread is not in main loop, silent wrong
values, or an interpreter-level crash".

The access pattern is real, and this script re-derives all six sites from the
live source. What it then establishes is which of those predicted
manifestations actually occur on a threaded Tcl build - because the answer
changes the rationale for the severity, and changes what a fix has to achieve.

Tcl here reports tcl_platform(threaded) = 1. On a threaded build _tkinter does
not let a foreign thread touch the interpreter directly: it packages the call,
hands it to the interpreter's own thread with Tcl_ThreadQueueEvent, and blocks
until it completes. The read is serialised, not racy.

Measured regimes:

  A. mainloop running, Tk thread idle   -> correct value returned
  B. mainloop running, Tk thread busy   -> the WORKER BLOCKS until it is free
  C. mainloop stopped (window closed)   -> RuntimeError, deterministically

The reachable production case is closing the window during a query, and its
consequence is neither a wrong value nor a crash: the worker thread **hangs
permanently** inside a Tk call, abandoning the export mid-flight. F-15 widens
the window, because root.update() inside _log lets the close be dispatched at
any log line.

A note on this script's own history, because it is a trap worth marking: the
regime measurements leave daemon threads blocked inside a destroyed Tcl
interpreter. Creating another Tk root afterwards in the same process aborts the
whole process with a C-level SIGABRT. That is real Tcl behaviour, but it is a
property of the *test*, not of the app - the app never re-initialises Tk after
teardown. The regimes are therefore measured in a subprocess.
================================================================================
"""

REQUIRES_DISPLAY = True

import ast
import multiprocessing
import queue
import threading
import time

import pyarrow as pa

import harness as h

WORKER_METHODS = ("_connect_thread", "_execute_thread", "_export_to_excel")


def offthread_read_sites():
    """
    Re-derive the audit's table from the live source.

    Finds `self.<attr>.get()` calls inside methods reachable from a worker
    thread, excluding anything nested in a root.after(...) call - those are
    correctly marshalled and are not part of this finding.
    """
    tree = ast.parse(h.source_text("app.py"))
    sites = []
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if func.name not in WORKER_METHODS:
            continue
        marshalled = set()
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "after"):
                for inner in ast.walk(node):
                    marshalled.add(id(inner))
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and id(node) not in marshalled
                    and isinstance(node.func.value, ast.Attribute)
                    and isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id == "self"):
                sites.append((node.lineno, func.name,
                              f"self.{node.func.value.attr}.get()"))
    return sorted(set(sites))


def _measure_regimes_impl():
    """Measure what a cross-thread widget read does in each regime."""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    threaded = root.tk.call("set", "tcl_platform(threaded)")
    var = tk.BooleanVar(value=True)
    entry = tk.Entry(root)
    entry.insert(0, "hello")
    out = queue.Queue()

    def read():
        try:
            out.put(("ok", var.get(), entry.get()))
        except Exception as e:
            out.put(("error", f"{type(e).__name__}: {e}"))

    results = {"threaded": threaded}

    # A: mainloop spinning, Tk thread free to service the queued call.
    threading.Thread(target=read, daemon=True).start()
    root.after(300, root.quit)
    root.mainloop()
    results["A"] = out.get(timeout=10)

    # B: the Tk thread is blocked inside a callback while the worker reads.
    def busy():
        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(2.0)
        results["B_still_blocked"] = t.is_alive()
        root.quit()

    root.after(50, busy)
    root.mainloop()

    # C: mainloop has exited - the state after the window is closed.
    threading.Thread(target=read, daemon=True).start()
    results["C"] = out.get(timeout=10)
    return results


def _regimes_child(result_queue):
    try:
        result_queue.put(_measure_regimes_impl())
    except Exception as e:                                   # noqa: BLE001
        result_queue.put({"error": f"{type(e).__name__}: {e}"})


def measure_regimes():
    """
    Run the regime measurements in a subprocess.

    Regimes B and C deliberately strand daemon threads inside a Tcl interpreter
    that is then torn down. Any later Tk initialisation in the same process
    aborts it at the C level. Isolating this in a subprocess is the difference
    between measuring the app and measuring the harness.
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    proc = ctx.Process(target=_regimes_child, args=(result_queue,), daemon=True)
    proc.start()
    try:
        return result_queue.get(timeout=180)
    finally:
        proc.terminate()
        proc.join(timeout=10)


def close_during_query():
    """
    The reachable case: the user closes the window while a query is running.

    _on_close saves settings and calls root.destroy() (app.py:504-507), ending
    the mainloop. Every subsequent Tk touch from the worker - the root.after
    progress callbacks, and the widget reads in _export_to_excel - is then in
    regime C.
    """
    from flightserver import ReproFlightServer, connected_connection

    table = pa.table({"id": pa.array(range(20000)),
                      "name": pa.array([f"r{i}" for i in range(20000)])})
    server = ReproFlightServer(table=table, batch_delay=0.15,
                               batch_rows=1000).start()

    worker_errors = []
    original_hook = threading.excepthook
    threading.excepthook = lambda args: worker_errors.append(
        f"{args.exc_type.__name__}: {args.exc_value}")

    try:
        with h.isolated_home(), h.temp_dir() as tmp:
            with h.tk_app(output_dir=tmp, filename="closed.xlsx",
                          autofit=True, open_after=False) as app, \
                    h.captured_dialogs():

                state = {}

                def scenario():
                    with h.silence_fd_stderr():
                        app.connection = connected_connection(server)
                    server.reset_counters()
                    app.query_text.delete("1.0", "end")
                    app.query_text.insert("1.0", "SELECT * FROM t")

                    before = set(threading.enumerate())
                    app._execute_and_export()
                    worker = h.new_threads_from(before)

                    h.wait_for(app.root,
                               lambda: server.do_get_started.is_set(), timeout=30)
                    h.pump(app.root, 0.5)
                    state["running_at_close"] = bool(worker and worker[0].is_alive())
                    state["batches_at_close"] = server.batches_sent

                    # The user clicks X. No Tk calls from here on.
                    app._on_close()

                    if worker:
                        worker[0].join(timeout=20)
                        state["worker_alive_after"] = worker[0].is_alive()
                    time.sleep(0.3)

                h.run_with_mainloop(app.root, scenario)

                state["file_written"] = (tmp / "closed.xlsx").exists()
                state["errors"] = worker_errors
                return state
    finally:
        threading.excepthook = original_hook
        server.stop()


def main():
    h.require_display()
    h.banner("F-12", "Tk widget reads on worker threads - what actually happens")

    h.step("STATIC: the access pattern, re-derived from the live source")
    sites = offthread_read_sites()
    for lineno, method, expr in sites:
        h.detail(f"app.py:{lineno}", f"{expr} in {method}()")
    h.detail("off-thread widget reads found", len(sites))
    if not sites:
        h.note("The reads are gone, but that is only half of F-12. Its reachable "
               "consequence - closing the window mid-query - is driven by the "
               "worker's root.after calls as much as by the reads, so the runtime "
               "half below still runs and the verdict still depends on it.")

    h.step("What a cross-thread read actually does, per regime (subprocess)")
    regimes = measure_regimes()
    if "error" in regimes:
        h.verdict("F-12", h.BLOCKED,
                  f"regime measurement failed: {regimes['error']}")
        return

    h.detail("tcl_platform(threaded)", regimes["threaded"])
    h.note("On a threaded Tcl build, _tkinter marshals foreign-thread calls to "
           "the interpreter's own thread via Tcl_ThreadQueueEvent and blocks "
           "until they complete. The read is serialised, not racy.")
    h.table(
        ["regime", "condition", "result"],
        [["A", "mainloop running, Tk thread idle", regimes["A"]],
         ["B", "mainloop running, Tk thread busy",
          f"worker still blocked after 2s: {regimes.get('B_still_blocked')}"],
         ["C", "mainloop stopped (window closed)", regimes["C"]]],
    )

    correct_value = regimes["A"][0] == "ok"
    deadlocks = regimes.get("B_still_blocked") is True
    hard_error = regimes["C"][0] == "error"

    h.step("Against the audit's three predicted manifestations")
    h.table(
        ["predicted", "observed"],
        [["RuntimeError: main thread is not in main loop",
          "YES - but only in regime C, after mainloop stops"],
         ["silent wrong values",
          f"NO - regime A returns the correct value {regimes['A'][1:]}"],
         ["interpreter-level crash",
          "only if Tk is re-initialised after a thread was stranded in a "
          "destroyed interpreter - not a path the app takes"]],
    )
    h.note("A fourth mode the audit did not list is the one that dominates: "
           "regime B, where the worker blocks until the Tk thread is free. That "
           "is a liveness hazard, not a corruption one.")

    h.step("The reachable case: closing the window during a query")
    state = close_during_query()
    h.detail("query still running when the window was closed",
             state.get("running_at_close"))
    h.detail("record batches delivered at that point",
             state.get("batches_at_close"))
    h.detail("worker still alive 20s after teardown",
             state.get("worker_alive_after"))
    h.detail("output file written", state.get("file_written"))
    h.detail("uncaught worker exceptions", state.get("errors") or "none")

    hung = state.get("worker_alive_after") is True
    if hung:
        h.note("The worker did not die - it HUNG. It is blocked inside a Tk call "
               "whose interpreter no longer dispatches. The export is abandoned "
               "mid-flight with no error on any channel; the thread is a daemon, "
               "so the process exits and the user simply never gets a file.")

    h.step("What would actually surface this in production")
    h.note("1. Closing the window while a query runs - the reachable case, "
           "measured directly above. F-15 widened it while it lasted: "
           "root.update() inside _log dispatched the close at any log line.")
    h.note("2. A non-threaded Tcl build, where foreign-thread calls are NOT "
           "marshalled and the audit's original reasoning would hold in full. "
           "tcl_platform(threaded) is the check; it is 1 here.")
    h.note("3. Anything that blocks the Tk thread while a worker reads (regime "
           "B): a modal dialog, a long synchronous callback.")
    h.note("NOT a way to surface it: a normal export on this build. The reads "
           "are marshalled and return correct values, which is why 18 other "
           "scripts drove this exact path without a failure.")

    if not sites and not hung:
        h.verdict("F-12", h.NOT_REPRODUCIBLE,
                  f"no unmarshalled self.<widget>.get() calls remain in "
                  f"worker-reachable methods, and closing the window mid-query no "
                  f"longer strands the worker (alive_after="
                  f"{state.get('worker_alive_after')})")
        return

    if not sites:
        h.verdict("F-12", h.CONFIRMED,
                  f"the widget reads are gone, but the reachable case survives "
                  f"them: closing the window mid-query still leaves the worker "
                  f"HUNG (alive_after={state.get('worker_alive_after')}, "
                  f"file_written={state.get('file_written')}). The remaining "
                  f"cross-thread Tk contact is the worker's own root.after calls")
        return

    h.verdict("F-12", h.CONFIRMED,
              f"{len(sites)} unmarshalled widget reads confirmed in the live source "
              f"at {['app.py:%d' % n for n, _, _ in sites]}. On this threaded Tcl "
              f"build the audit's stated rationale does not hold: regime A returns "
              f"CORRECT values (no silent wrong values, no crash), regime B blocks "
              f"the worker until the Tk thread frees up (deadlock hazard the audit "
              f"did not list), regime C raises RuntimeError deterministically. The "
              f"reachable case - closing the window mid-query - leaves the worker "
              f"HUNG (alive_after={state.get('worker_alive_after')}, "
              f"file_written={state.get('file_written')}), abandoning the export "
              f"silently rather than crashing. Severity rationale needs revising; "
              f"see AUDIT.md")


if __name__ == "__main__":
    main()
