"""
================================================================================
F-14 - disconnect() can null the client out from under a running query   (High)
================================================================================
SOURCE-only in AUDIT.md, and the finding the audit expected to stay blocked for
want of a live Dremio endpoint. tests/flightserver.py removes that obstacle: a
real Arrow Flight server with a deliberately slow get_flight_info holds the
worker inside the query while the main thread clicks Disconnect.

The original race:

    _execute_and_export disables only execute_btn. The Connect/Disconnect
    button is never disabled during execution, so the user can click Disconnect
    while _execute_thread is inside execute_query. disconnect() sets
    self.client = None on the main thread, and execute_query re-read self.client
    at each step - get_flight_info, then do_get. A disconnect landing between
    them produced AttributeError: 'NoneType' object has no attribute 'do_get',
    surfaced as a bare dialog with no explanation.

It was more likely than it looked, because the natural recovery sequence for a
slow query was to press Stop, discover it did nothing (F-13), and click
Disconnect instead.

The fix keeps the affordance and removes the race. Disconnect stays clickable
during execution - it is the escape hatch, and with F-13 fixed there is now a
cancellation path for it to use. Three parts:

  1. execute_query binds client and bearer_token to locals once, so a
     disconnect cannot dismantle a call that is already under way.
  2. DremioConnection.disconnect() cancels any in-flight read first, so the
     read ends deliberately instead of being orphaned.
  3. The UI asks before doing it, then cancels through the same path as Stop.

Both windows are exercised below: disconnecting during planning (inside
get_flight_info, before any reader exists) and disconnecting mid-stream.
================================================================================
"""

REQUIRES_DISPLAY = True

import threading

import pyarrow as pa

import harness as h

INFO_DELAY = 3.0       # holds the worker inside get_flight_info
DISCONNECT_AFTER = 1.0
STREAM_ROWS = 40_000
STREAM_BATCH = 1_000
STREAM_DELAY = 0.15


def disconnect_during(phase):
    """
    Run a query and click Disconnect part-way through it.

    phase 'planning' holds the worker inside get_flight_info, before a reader
    exists. phase 'streaming' lets the stream start and interrupts that.

    Returns a dict of what was observed.
    """
    from flightserver import ReproFlightServer, connected_connection

    if phase == "planning":
        table = pa.table({"id": pa.array(range(500))})
        server = ReproFlightServer(table=table, info_delay=INFO_DELAY).start()
    else:
        table = pa.table({
            "id": pa.array(range(STREAM_ROWS)),
            "payload": pa.array([f"row-{i}" for i in range(STREAM_ROWS)]),
        })
        server = ReproFlightServer(table=table, batch_delay=STREAM_DELAY,
                                   batch_rows=STREAM_BATCH).start()

    observed = {"phase": phase}
    try:
        with h.isolated_home(), h.temp_dir() as tmp:
            with h.tk_app(output_dir=tmp, filename="raced.xlsx",
                          autofit=False, open_after=False) as app, \
                    h.captured_dialogs() as dialogs:

                exported = tmp / "raced.xlsx"

                def scenario():
                    with h.silence_fd_stderr():
                        app.connection = connected_connection(server)
                    server.reset_counters()

                    app.query_text.delete("1.0", "end")
                    app.query_text.insert("1.0", "SELECT * FROM slow_thing")

                    before = set(threading.enumerate())
                    app._execute_and_export()
                    worker = h.new_threads_from(before)

                    observed["connect_clickable"] = \
                        str(app.connect_btn["state"]) == "normal"

                    if phase == "planning":
                        h.pump(app.root, DISCONNECT_AFTER)
                        observed["reader_active"] = server.do_get_started.is_set()
                    else:
                        h.wait_for(app.root,
                                   lambda: server.do_get_started.is_set(),
                                   timeout=30)
                        h.pump(app.root, 1.0)
                        observed["reader_active"] = True
                        observed["batches_at_disconnect"] = server.batches_sent

                    observed["worker_running_at_disconnect"] = bool(
                        worker and worker[0].is_alive())

                    # This is exactly _toggle_connection's disconnect branch.
                    app._toggle_connection()
                    observed["asked_first"] = len(dialogs["askyesno"]) == 1
                    observed["prompt"] = (dialogs["askyesno"][-1][1]
                                          if dialogs["askyesno"] else None)
                    observed["client_after"] = app.connection.client
                    observed["is_connected_after"] = app.connection.is_connected

                    h.wait_for(app.root,
                               lambda: worker and not worker[0].is_alive(),
                               timeout=60)
                    h.pump(app.root, 0.8)

                    log = app.log_text.get("1.0", "end")
                    observed["errors"] = list(dialogs["error"])
                    observed["success"] = list(dialogs["info"])
                    observed["error_lines"] = [ln.strip() for ln in log.splitlines()
                                               if "ERROR" in ln]
                    observed["log_cancelled"] = "Execution cancelled" in log
                    observed["file_written"] = exported.exists()
                    observed["kept_partial"] = app.df is not None
                    observed["buttons_restored"] = \
                        str(app.execute_btn["state"]) == "normal"
                    observed["total_batches"] = server.batches_sent

                h.run_with_mainloop(app.root, scenario)
    finally:
        server.stop()
    return observed


def report(observed):
    phase = observed["phase"]
    h.step(f"Disconnect during {phase}")
    h.detail("Disconnect clickable during execution",
             observed.get("connect_clickable"))
    h.detail("a reader was live at that moment", observed.get("reader_active"))
    if "batches_at_disconnect" in observed:
        h.detail("record batches delivered so far",
                 observed["batches_at_disconnect"])
    h.detail("worker running when Disconnect was clicked",
             observed.get("worker_running_at_disconnect"))
    h.detail("asked before disconnecting", observed.get("asked_first"))
    if observed.get("prompt"):
        h.detail("prompt", observed["prompt"].replace("\n", " | ")[:110])
    h.detail("connection.client afterwards", observed.get("client_after"))
    h.detail("error dialogs", len(observed.get("errors") or []))
    for title, message in observed.get("errors") or []:
        h.detail(f"  dialog [{title}]", message.replace("\n", " ")[:110])
    for line in observed.get("error_lines") or []:
        h.detail("  log", line)
    h.detail("log reports a cancellation", observed.get("log_cancelled"))
    h.detail("success dialog", bool(observed.get("success")))
    h.detail("file written", observed.get("file_written"))
    h.detail("partial result kept", observed.get("kept_partial"))
    h.detail("UI usable afterwards", observed.get("buttons_restored"))


def evidence_of(observed):
    messages = " | ".join(m for _, m in observed.get("errors") or [])
    return messages + " " + " ".join(observed.get("error_lines") or [])


def main():
    h.require_display()
    h.banner("F-14", "Disconnect during a running query")

    runs = [disconnect_during("planning"), disconnect_during("streaming")]
    for observed in runs:
        report(observed)

    nonetype = [o["phase"] for o in runs
                if "'NoneType' object has no attribute" in evidence_of(o)]
    other_errors = [o["phase"] for o in runs
                    if (o.get("errors") or o.get("error_lines"))
                    and o["phase"] not in nonetype]
    clickable = all(o.get("connect_clickable") for o in runs)
    asked = all(o.get("asked_first") for o in runs)
    cancelled = all(o.get("log_cancelled") for o in runs)
    no_file = not any(o.get("file_written") for o in runs)
    no_partial = not any(o.get("kept_partial") for o in runs)
    usable = all(o.get("buttons_restored") for o in runs)

    streaming = next(o for o in runs if o["phase"] == "streaming")
    expected_batches = (STREAM_ROWS + STREAM_BATCH - 1) // STREAM_BATCH
    interrupted = streaming.get("total_batches", expected_batches) < expected_batches

    h.step("Contract check")
    h.detail("1. no AttributeError on a nulled client",
             not nonetype or f"STILL PRESENT in {nonetype}")
    h.detail("2. no unexplained error dialog",
             not other_errors or f"errors in {other_errors}")
    h.detail("3. the escape hatch is still there", clickable)
    h.detail("4. the user is asked first", asked)
    h.detail("5. reported as a cancellation", cancelled)
    h.detail("6. the transfer actually stopped",
             f"{interrupted} ({streaming.get('total_batches')} of "
             f"{expected_batches} batches)")
    h.detail("7. nothing kept, nothing written", no_partial and no_file)
    h.detail("8. the UI is usable afterwards", usable)

    h.note("Both windows were exercised: inside get_flight_info, where no "
           "reader exists yet and the cancellation flag is what catches it, and "
           "mid-stream, where the reader is interrupted directly.")
    h.note("Measured against a local Flight server, not against Dremio. The "
           "client-side race is settled here; Dremio's own handling of an "
           "abandoned job is not - see repro_f13_stop_button.py.")

    if (not nonetype and not other_errors and clickable and asked
            and cancelled and no_file and no_partial and interrupted):
        h.verdict("F-14", h.NOT_REPRODUCIBLE,
                  f"disconnecting mid-query is a cancellation rather than a race: "
                  f"no 'NoneType' AttributeError in either window, execute_query "
                  f"holds its own client reference, the user is asked before it "
                  f"happens, the transfer stops early "
                  f"({streaming.get('total_batches')} of {expected_batches} "
                  f"batches), and nothing is kept or written. Disconnect stays "
                  f"clickable during execution on purpose")
    elif nonetype:
        h.verdict("F-14", h.CONFIRMED,
                  f"clicking Disconnect mid-query still nulls the client under the "
                  f"worker in {nonetype}: {evidence_of(runs[0]).strip()[:200]}")
    else:
        h.verdict("F-14", h.CONFIRMED,
                  f"disconnect handling incomplete: asked={asked} "
                  f"cancelled={cancelled} interrupted={interrupted} "
                  f"no_file={no_file} no_partial={no_partial} "
                  f"other_errors={other_errors}")


if __name__ == "__main__":
    main()
