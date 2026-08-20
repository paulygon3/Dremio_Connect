"""
================================================================================
F-13 - The Stop button sets a flag nothing reads   (Medium)
================================================================================
Originally: self.is_running was assigned in three places and read in none.
_stop_execution logged "Execution cancelled" - an affirmatively false statement
to the user - while the query ran to completion, all rows arrived, the file was
written and the success dialog appeared.

The agreed fix is real cancellation, not a nicer message:

  1. read_all() - one blocking call with no point at which a flag can be
     observed - is replaced by a read_chunk() loop over the FlightStreamReader.
  2. Stop sets a threading.Event the loop checks between batches, AND calls
     FlightStreamReader.cancel() so a read already blocked waiting for the next
     batch is interrupted immediately rather than at the end of the stream.
  3. A cancelled query keeps nothing: the batches read so far are discarded, no
     DataFrame is built, no file is written, no success is reported.

This script checks each, against a real Flight server, through the app's real
Execute and Stop handlers on a real worker thread.

--------------------------------------------------------------------------------
What is cancelled, and what is not
--------------------------------------------------------------------------------
cancel() cancels the *Flight result stream* - the gRPC call carrying the rows.
Measured here: after cancel the server stops being pulled and stops producing
batches, so the transfer genuinely ends early.

Whether the *server-side query* also stops is a different question, and this
harness cannot answer it. tests/flightserver.py is not Dremio: its do_get is a
generator that simply stops being consumed. A real Dremio job may keep running
to completion in the engine after the client hangs up, holding queue slot and
memory. Do not read "Stop works" as "the Dremio job was killed".

To settle it against a real endpoint: press Stop on a long query, then check the
job in Dremio's Jobs UI (or SELECT * FROM sys.jobs) and see whether its state is
CANCELED or COMPLETED. That result belongs in AUDIT.md; it is not derivable
here.
================================================================================
"""

REQUIRES_DISPLAY = True

import ast
import threading
import time

import pyarrow as pa

import harness as h

ROWS = 40_000
BATCH_ROWS = 1_000     # 40 record batches
BATCH_DELAY = 0.15     # ~6s of streaming, ample to press Stop part-way through


def readers_of(attr):
    """
    Find every load of self.<attr> in app.py, distinguishing it from stores.

    A grep cannot tell `self.is_running = False` from `if self.is_running:`, and
    the original finding was that only the former existed.
    """
    tree = ast.parse(h.source_text("app.py"))
    stores, loads = [], []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == attr
                and isinstance(node.value, ast.Name) and node.value.id == "self"):
            (stores if isinstance(node.ctx, ast.Store) else loads).append(node.lineno)
    return sorted(stores), sorted(loads)


def blocking_read_sites():
    """
    read_all() calls in connection.py, named by the method they sit in.

    Only the one in the query path matters. _test_connection's `SELECT 1` is a
    single row on connect and has nothing to cancel, so it is reported
    separately rather than counted against the fix.
    """
    tree = ast.parse(h.source_text("connection.py"))
    sites = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        for node in ast.walk(func):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read_all"):
                sites.append((func.name, node.lineno))
    return sites


def main():
    h.require_display()
    h.banner("F-13", "Stop must actually stop the read")

    from flightserver import ReproFlightServer, connected_connection

    h.step("STATIC: is there anything to read the cancellation flag?")
    run_stores, run_loads = readers_of("is_running")
    cancel_stores, cancel_loads = readers_of("cancel_requested")
    h.detail("is_running assignments", [f"app.py:{n}" for n in run_stores])
    h.detail("is_running reads",
             [f"app.py:{n}" for n in run_loads] if run_loads else "NONE")
    h.detail("cancel_requested reads",
             [f"app.py:{n}" for n in cancel_loads] if cancel_loads else "NONE")
    reads_flag = bool(run_loads) or bool(cancel_loads)

    streaming = h.grep_source(r"read_chunk\(\)", ["connection.py"])
    cancel_call = h.grep_source(r"reader\.cancel\(\)", ["connection.py"])
    read_all_sites = blocking_read_sites()
    query_path_blocking = [s for s in read_all_sites
                           if s[0] in ("execute_query", "_read_stream")]
    h.detail("connection.py reads batch by batch", bool(streaming))
    h.detail("connection.py can cancel a live reader", bool(cancel_call))
    h.detail("read_all() in the query path",
             query_path_blocking or "none - the streaming loop replaced it")
    h.detail("read_all() elsewhere",
             [f"{name}() at connection.py:{n}" for name, n in read_all_sites
              if (name, n) not in query_path_blocking]
             or "none")

    table = pa.table({
        "id": pa.array(range(ROWS)),
        "payload": pa.array([f"row-{i}" for i in range(ROWS)]),
    })
    server = ReproFlightServer(table=table, batch_delay=BATCH_DELAY,
                               batch_rows=BATCH_ROWS).start()

    try:
        with h.isolated_home(), h.temp_dir() as tmp:
            with h.tk_app(output_dir=tmp, filename="stopped.xlsx",
                          autofit=False, open_after=False) as app, \
                    h.captured_dialogs() as dialogs:

                exported = tmp / "stopped.xlsx"
                observed = {}

                def scenario():
                    h.step("Connecting the real app to the Flight server")
                    with h.silence_fd_stderr():
                        app.connection = connected_connection(server)
                    h.detail("app.connection.is_connected",
                             app.connection.is_connected)
                    # connect() runs _test_connection, which streams the whole
                    # table once on its own. Discount it.
                    server.reset_counters()

                    app.query_text.delete("1.0", "end")
                    app.query_text.insert("1.0", "SELECT * FROM slow_table")

                    h.step("Pressing Execute (the real handler, real worker thread)")
                    before = set(threading.enumerate())
                    app._execute_and_export()
                    worker = h.new_threads_from(before)
                    h.detail("worker thread started", bool(worker))
                    h.detail("is_running after Execute", app.is_running)
                    h.detail("stop_btn state", str(app.stop_btn["state"]))

                    h.step("Pressing Stop while the stream is still arriving")
                    h.wait_for(app.root,
                               lambda: server.do_get_started.is_set(), timeout=30)
                    h.pump(app.root, 1.0)
                    observed["batches_at_stop"] = server.batches_sent
                    observed["stream_done_at_stop"] = server.do_get_finished.is_set()
                    h.detail("record batches delivered so far",
                             observed["batches_at_stop"])
                    h.detail("stream already finished?",
                             observed["stream_done_at_stop"])

                    t_stop = time.time()
                    app._stop_execution()
                    observed["cancel_requested"] = app.cancel_requested.is_set()
                    h.detail("cancel_requested after Stop",
                             observed["cancel_requested"])

                    h.step("What actually happened next")
                    finished = h.wait_for(
                        app.root,
                        lambda: worker and not worker[0].is_alive(),
                        timeout=120)
                    observed["worker_exit_seconds"] = time.time() - t_stop
                    h.pump(app.root, 1.0)

                    h.detail("worker finished", finished)
                    h.detail("worker exited within",
                             f"{observed['worker_exit_seconds']:.2f}s of Stop")
                    h.detail("record batches delivered in total",
                             server.batches_sent)
                    h.detail("of an expected",
                             (ROWS + BATCH_ROWS - 1) // BATCH_ROWS)
                    h.detail("server finished sending the whole stream",
                             server.do_get_finished.is_set())

                    # Let the abandoned generator run on, if it is going to.
                    time.sleep(1.5)
                    observed["batches_after_settle"] = server.batches_sent
                    h.detail("batches after a further 1.5s",
                             observed["batches_after_settle"])

                    h.detail("output file written", exported.exists())
                    h.detail("app.df (partial result kept?)",
                             None if app.df is None else f"{len(app.df):,} rows")
                    h.detail("success dialog shown", bool(dialogs["info"]))
                    h.detail("error dialog shown", bool(dialogs["error"]))
                    if dialogs["error"]:
                        h.detail("  error text",
                                 dialogs["error"][0][1].replace("\n", " ")[:100])

                    log = app.log_text.get("1.0", "end")
                    observed["log_says_cancelled"] = "cancelled" in log.lower()
                    observed["log_says_no_file"] = "no file was written" in log
                    h.detail("log tells the user nothing was written",
                             observed["log_says_no_file"])

                    observed["full_stream"] = server.do_get_finished.is_set()
                    observed["kept_partial"] = app.df is not None
                    observed["file_written"] = exported.exists()
                    observed["success_shown"] = bool(dialogs["info"])
                    observed["error_shown"] = bool(dialogs["error"])
                    observed["buttons_restored"] = (
                        str(app.execute_btn["state"]) == "normal")

                # A live mainloop is mandatory: the worker marshals every UI
                # update with root.after(), which raises from a non-Tk thread
                # unless the main thread is inside mainloop().
                h.run_with_mainloop(app.root, scenario)
    finally:
        server.stop()

    batches_at_stop = observed.get("batches_at_stop")
    total_batches = observed.get("batches_after_settle")
    expected = (ROWS + BATCH_ROWS - 1) // BATCH_ROWS

    if observed.get("stream_done_at_stop"):
        h.note("NOTE: the server had already streamed everything before Stop was "
               "pressed, so this run does not prove mid-flight behaviour. Raise "
               "BATCH_DELAY or ROWS.")

    h.step("Contract check")
    interrupted = (not observed.get("full_stream")) and total_batches < expected
    prompt = observed.get("worker_exit_seconds", 999) < 2.0
    no_partial = not observed.get("kept_partial")
    no_file = not observed.get("file_written")
    quiet = not observed.get("success_shown") and not observed.get("error_shown")

    h.detail("1. the transfer was interrupted, not awaited",
             f"{interrupted} ({total_batches} of {expected} batches)")
    h.detail("2. Stop took effect immediately",
             f"{prompt} (worker exited {observed.get('worker_exit_seconds', 0):.2f}s "
             f"after Stop; a full stream would have taken "
             f"~{(expected - batches_at_stop) * BATCH_DELAY:.0f}s more)")
    h.detail("3. no partial result kept", no_partial)
    h.detail("4. no file written", no_file)
    h.detail("5. reported as cancellation, not success and not an error", quiet)
    h.detail("6. the UI is usable again", observed.get("buttons_restored"))

    h.step("Scope of the cancellation")
    h.note("The Flight result stream stopped - measured above. Whether Dremio "
           "also kills the server-side job is NOT established by this harness; "
           "the local server's do_get generator is simply no longer consumed. "
           "Verify against a real endpoint via sys.jobs before claiming it.")

    if (reads_flag and streaming and not query_path_blocking and interrupted
            and prompt and no_partial and no_file and quiet):
        h.verdict("F-13", h.NOT_REPRODUCIBLE,
                  f"Stop now interrupts the read: {total_batches} of {expected} "
                  f"batches were delivered, the worker exited "
                  f"{observed.get('worker_exit_seconds', 0):.2f}s after Stop rather "
                  f"than streaming to the end, no partial DataFrame was kept "
                  f"(app.df is None), no file was written, and the run was "
                  f"reported as cancelled rather than as success or as an error. "
                  f"Server-side job cancellation is out of this harness's scope - "
                  f"see the note above")
    elif not reads_flag:
        h.verdict("F-13", h.CONFIRMED,
                  f"no cancellation flag is read anywhere: is_running stores="
                  f"{run_stores} loads={run_loads or 'NONE'}")
    else:
        h.verdict("F-13", h.CONFIRMED,
                  f"cancellation incomplete: interrupted={interrupted} "
                  f"({total_batches}/{expected} batches) immediate={prompt} "
                  f"no_partial={no_partial} no_file={no_file} "
                  f"quiet_report={quiet}")


if __name__ == "__main__":
    main()
