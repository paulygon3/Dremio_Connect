"""
================================================================================
F-30 - Token lifetime in memory is unbounded   (Medium)
================================================================================
SOURCE-only in AUDIT.md, then executed in Stage 0. The PAT was held in at least
four places for the life of the process:

    self.conn_fields['token'] ttk.Entry     until the window closes - never
                                            cleared after a successful connect
    _connect_thread(... token) frame        until the thread ends, and longer
                                            if a traceback keeps the frame
    self.middleware.call_credential         until disconnect()
    self.bearer_token                       until disconnect()

disconnect() already cleared the last two by rebinding to None, which was the
right instinct. Nothing cleared the Entry, and nothing scrubbed the frames.

What the fix does, and what it deliberately does not
----------------------------------------------------
The frames are scrubbed on every path, including the one that raises. This is
not decoration: a frame outlives its call whenever an exception carries it away
- the traceback holds the frame, the frame holds its locals - so the PAT stays
reachable for as long as anything holds that exception. Rebinding the parameter
in a `finally` empties the slot, which is measured below rather than asserted.

The Entry is cleared after a successful connect ONLY when "Remember token" is
off. With it on, the PAT is deliberately written to disk, so wiping the widget
would be theatre - it would still be one file read away, and the user asked for
exactly that. With it off, the user has said they do not want it kept, and
keeping it in a widget for the rest of the session does not honour that. Both
states are checked here, because "the token is gone" would be the wrong result
for one of them.

Retention is measured, not inferred: every live frame belonging to app.py or
connection.py is scanned for the PAT, which answers "is it still reachable"
directly rather than by reading the source.

Also checked, because the audit states it and "Save Log" writes the panel to a
user-chosen file: the token is NOT written to the log panel on any path here.
The exposure is storage (F-28) and retention, not logging.

This runs a real connect() against a local Flight server, so the middleware and
bearer token are genuinely populated rather than simulated.
================================================================================
"""

REQUIRES_DISPLAY = True

import gc
import threading
import types

import pyarrow as pa

import harness as h

# Built at runtime so it is not an interned literal shared with this module's
# constants - the scan below is looking for THIS object's value.
TOKEN = "".join(["super-secret", "-PAT-", "value-30"])
USERNAME = "alice"

APP_SOURCES = ("app.py", "connection.py")


def frames_holding(value):
    """
    Every live frame belonging to the app that still has `value` in its locals.

    This is the direct question - is the PAT still reachable - rather than the
    indirect one about what the source says. The repro's own frames are
    excluded: this module necessarily holds the token, and reporting itself
    would drown the answer.
    """
    gc.collect()
    hits = []
    for obj in gc.get_objects():
        if not isinstance(obj, types.FrameType):
            continue
        filename = obj.f_code.co_filename
        if not any(filename.endswith(src) for src in APP_SOURCES):
            continue
        try:
            local_items = list(obj.f_locals.items())
        except Exception:
            continue
        for name, local in local_items:
            if isinstance(local, str) and local == value:
                hits.append(f"{filename.split('/')[-1]}:"
                            f"{obj.f_code.co_name}({name})")
    return hits


def connect_through_the_ui(app, server, remember):
    """Drive the real _connect / _connect_thread and report what is retained."""
    observed = {}

    def scenario():
        h.set_entry(app.conn_fields["hostname"], "localhost")
        h.set_entry(app.conn_fields["port"], str(server.port))
        h.set_entry(app.conn_fields["username"], USERNAME)
        h.set_entry(app.conn_fields["token"], TOKEN)
        app.use_tls.set(False)
        app.remember_token.set(remember)

        before = set(threading.enumerate())
        with h.silence_fd_stderr():
            app._connect()
            worker = h.new_threads_from(before)
            h.wait_for(app.root,
                       lambda: worker and not worker[0].is_alive(),
                       timeout=60)
            h.pump(app.root, 0.5)

        observed["connected"] = app.connection.is_connected
        observed["entry_after_connect"] = app.conn_fields["token"].get() == TOKEN
        observed["frames_after_connect"] = frames_holding(TOKEN)

        app._disconnect()
        observed["bearer_cleared"] = app.connection.bearer_token is None
        observed["middleware_cleared"] = app.connection.middleware is None
        observed["entry_after_disconnect"] = \
            app.conn_fields["token"].get() == TOKEN
        observed["in_log"] = TOKEN in app.log_text.get("1.0", "end")

    h.run_with_mainloop(app.root, scenario)
    return observed


def failed_connect_does_not_retain(server):
    """
    The path that matters most, because it is the one that keeps the frame.

    connection.connect is called directly with a token but a port nothing is
    listening on, and the exception is held afterwards - which is what an error
    handler that logs a traceback, or a debugger, would do. Every frame in that
    traceback is then searched for the PAT.
    """
    h.step("A failed connect: does the traceback still carry the PAT?")
    h.add_src_to_path()
    from connection import DremioConnection

    conn = DremioConnection()
    held = None
    with h.silence_fd_stderr():
        try:
            conn.connect(hostname="localhost", port=str(server.port + 1),
                         username=USERNAME, token=TOKEN, use_tls=False,
                         on_status=lambda m: None)
        except Exception as e:
            held = e

    rows = []
    carrying = []
    tb = held.__traceback__ if held else None
    while tb:
        frame = tb.tb_frame
        name = frame.f_code.co_filename.split("/")[-1]
        token_local = frame.f_locals.get("token", "<absent>")
        if isinstance(token_local, str) and token_local == TOKEN:
            carrying.append(f"{name}:{frame.f_code.co_name}")
            shown = "THE PAT"
        else:
            shown = repr(token_local)
        rows.append([f"{name}:{frame.f_code.co_name}", shown])
        tb = tb.tb_next

    h.table(["frame in the traceback", "its `token` local"], rows)
    h.detail("exception raised", type(held).__name__ if held else "none")
    h.detail("frames still carrying the PAT", carrying or "none")
    return held is not None, carrying


def main():
    h.require_display()
    h.banner("F-30", "Token retention in memory after connect and disconnect")

    from flightserver import ReproFlightServer

    server = ReproFlightServer(table=pa.table({"id": pa.array(range(10))})).start()

    results = {}
    try:
        with h.isolated_home(), h.temp_dir() as tmp:
            for remember in (False, True):
                label = "on" if remember else "off"
                h.step(f"Connecting with 'Remember token' {label.upper()}")
                with h.tk_app(output_dir=tmp) as app, h.captured_dialogs():
                    observed = connect_through_the_ui(app, server, remember)
                results[remember] = observed

                h.detail("connected", observed["connected"])
                h.detail("Entry still holds the PAT after connect",
                         observed["entry_after_connect"])
                h.detail("Entry still holds the PAT after disconnect",
                         observed["entry_after_disconnect"])
                h.detail("app/connection frames holding the PAT",
                         observed["frames_after_connect"] or "none")
                h.detail("derived credentials cleared by disconnect",
                         observed["bearer_cleared"]
                         and observed["middleware_cleared"])
                h.detail("PAT appears in the log panel", observed["in_log"])

            raised, carrying = failed_connect_does_not_retain(server)
    finally:
        server.stop()

    off, on = results[False], results[True]

    # With Remember off, the PAT must be gone from the form and from any frame.
    cleared_when_not_remembered = (
        off["connected"]
        and not off["entry_after_connect"]
        and not off["entry_after_disconnect"]
        and not off["frames_after_connect"]
    )
    # With Remember on, keeping it is the user's choice - but the frames must
    # still be clean, because that is retention nobody asked for.
    kept_when_remembered = on["connected"] and on["entry_after_connect"]
    frames_clean = not off["frames_after_connect"] and not on["frames_after_connect"]
    derived_cleared = all(r["bearer_cleared"] and r["middleware_cleared"]
                          for r in results.values())
    never_logged = not any(r["in_log"] for r in results.values())

    h.step("Contract check")
    h.detail("Remember OFF: form and frames cleared", cleared_when_not_remembered)
    h.detail("Remember ON: kept deliberately", kept_when_remembered)
    h.detail("no app frame holds the PAT either way", frames_clean)
    h.detail("failed connect leaves no PAT in the traceback",
             raised and not carrying)
    h.detail("disconnect clears the derived credentials", derived_cleared)
    h.detail("PAT never reaches the log panel", never_logged)

    if (cleared_when_not_remembered and kept_when_remembered and frames_clean
            and raised and not carrying and derived_cleared and never_logged):
        h.verdict("F-30", h.NOT_REPRODUCIBLE,
                  "retention is now bounded by what the user asked for: with "
                  "'Remember token' off the PAT is cleared from the form once it "
                  "has been used and is in no app frame afterwards; with it on "
                  "the form keeps it, which is the point of the setting and no "
                  "worse than the file it is already saved to. The frames are "
                  "scrubbed on both the success and the failure path - a held "
                  "exception's traceback no longer carries the PAT in any "
                  "connection.py frame - and disconnect still clears the derived "
                  "bearer token and middleware. The token reaches neither the log "
                  "panel nor, therefore, a saved log file")
    elif carrying:
        h.verdict("F-30", h.CONFIRMED,
                  f"a failed connect leaves the PAT reachable through the "
                  f"exception's traceback in {carrying} - it survives for as long "
                  f"as anything holds that exception")
    else:
        h.verdict("F-30", h.CONFIRMED,
                  f"the PAT is retained beyond its use: remember_off_cleared="
                  f"{cleared_when_not_remembered} "
                  f"frames_after_connect(off)={off['frames_after_connect']} "
                  f"frames_after_connect(on)={on['frames_after_connect']} "
                  f"entry_after_disconnect(off)={off['entry_after_disconnect']}")


if __name__ == "__main__":
    main()
