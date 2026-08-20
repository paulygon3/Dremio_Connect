"""
================================================================================
F-19 - Buttons latch permanently if Thread.start() fails   (Low)
================================================================================
SOURCE-only in AUDIT.md. Both handlers disable their buttons BEFORE starting the
worker, and the start() call sits outside any try:

    self.connect_btn.config(state='disabled', text="Connecting...")   app.py:640
    thread.start()                                                    app.py:649

    self.execute_btn.config(state='disabled')                         app.py:698
    self.stop_btn.config(state='normal', ...)                         app.py:699
    thread.start()                                                    app.py:704

If start() raises RuntimeError("can't start new thread"), connect_btn stays
disabled reading "Connecting..." forever, or execute_btn stays disabled with
stop_btn lit. The UI is stuck with no recovery short of restarting the app,
because the state reset lives only in the worker's finally - which never runs
if the worker never starts.

Low severity: it requires thread exhaustion. The general shape is the point, and
it is what this checks: any failure before the worker starts leaves the UI
latched.

Thread.start is patched to raise rather than actually exhausting the thread
table, which would destabilise the whole test run.
================================================================================
"""

REQUIRES_DISPLAY = True

import threading

import harness as h


class StubConnection:
    """Stands in for a connected DremioConnection so Execute gets past its guard."""
    is_connected = True

    def disconnect(self):
        self.is_connected = False


def with_failing_thread_start(fn):
    """Run fn() with Thread.start raising RuntimeError, as under thread exhaustion."""
    original = threading.Thread.start

    def failing_start(self):
        raise RuntimeError("can't start new thread")

    threading.Thread.start = failing_start
    try:
        return fn()
    finally:
        threading.Thread.start = original


def main():
    h.require_display()
    h.banner("F-19", "Buttons latch permanently if Thread.start() fails")

    h.step("STATIC: is the start() call guarded, and where is the reset?")
    for name, lineno, line in h.grep_source(
            r"thread\.start\(\)|config\(state='disabled'|finally:", ["app.py"]):
        h.detail(f"{name}:{lineno}", line)
    guarded = [x for x in h.grep_source(r"try:", ["app.py"])
               if 640 <= x[1] <= 650 or 697 <= x[1] <= 705]
    h.detail("try/except around either thread.start()", guarded or "NONE")

    with h.isolated_home(), h.temp_dir() as tmp:
        h.step("Case 1: Connect, with Thread.start() failing")
        with h.tk_app(output_dir=tmp) as app, h.captured_dialogs():
            h.set_entry(app.conn_fields["hostname"], "dremio.example.com")
            h.set_entry(app.conn_fields["port"], "32010")
            h.set_entry(app.conn_fields["username"], "alice")
            h.set_entry(app.conn_fields["token"], "pat-token")

            h.detail("connect_btn before", f"{app.connect_btn['state']} "
                     f"/ {app.connect_btn['text']!r}")

            escaped = None
            try:
                with_failing_thread_start(app._connect)
            except Exception as e:
                escaped = f"{type(e).__name__}: {e}"

            h.detail("exception escaped from the handler", escaped or "no")
            connect_state = str(app.connect_btn["state"])
            connect_text = str(app.connect_btn["text"])
            h.detail("connect_btn after", f"{connect_state} / {connect_text!r}")
            connect_latched = connect_state == "disabled"
            h.detail("=> latched (no way back without restarting)", connect_latched)

        h.step("Case 2: Execute, with Thread.start() failing")
        with h.tk_app(output_dir=tmp) as app2, h.captured_dialogs():
            app2.connection = StubConnection()
            app2.query_text.delete("1.0", "end")
            app2.query_text.insert("1.0", "SELECT 1")

            h.detail("execute_btn before", str(app2.execute_btn["state"]))
            h.detail("stop_btn before", str(app2.stop_btn["state"]))

            escaped2 = None
            try:
                with_failing_thread_start(app2._execute_and_export)
            except Exception as e:
                escaped2 = f"{type(e).__name__}: {e}"

            h.detail("exception escaped from the handler", escaped2 or "no")
            execute_state = str(app2.execute_btn["state"])
            stop_state = str(app2.stop_btn["state"])
            h.detail("execute_btn after", execute_state)
            h.detail("stop_btn after", stop_state)
            h.detail("is_running left set", app2.is_running)
            execute_latched = execute_state == "disabled" and stop_state == "normal"
            h.detail("=> latched (Execute dead, Stop lit, nothing running)",
                     execute_latched)

    h.step("Why it cannot recover")
    h.note("The state reset lives only in the worker's finally (app.py:738-743). "
           "If the worker never starts, it never runs.")

    if (connect_latched or execute_latched) and not guarded:
        h.verdict("F-19", h.CONFIRMED,
                  f"with Thread.start() raising, connect_btn is left "
                  f"{connect_state}/{connect_text!r} and execute_btn/stop_btn are left "
                  f"{execute_state}/{stop_state} with is_running still set; neither "
                  f"start() call is inside a try, and the only state reset is in the "
                  f"worker's finally, which never runs")
    elif guarded:
        h.verdict("F-19", h.NOT_REPRODUCIBLE,
                  f"thread.start() is now guarded: {guarded}")
    else:
        h.verdict("F-19", h.NOT_REPRODUCIBLE,
                  "buttons returned to a usable state after the failure")


if __name__ == "__main__":
    main()
