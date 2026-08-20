"""
================================================================================
F-15 - root.update() re-enters the event loop from inside callbacks   (Medium)
================================================================================
_log calls self.root.update() at app.py:518, and _set_status does the same at
app.py:606. update() processes the ENTIRE pending event queue - other queued
after callbacks and user input alike. Because _log is itself called from inside
after callbacks, every log line re-enters the event loop while an outer callback
is still on the stack.

Three consequences, in the audit's order of nastiness. This script demonstrates
the first and third directly, and the second (window closed mid-log) by queueing
the same destroy that _on_close performs:

  1. queued callbacks run out of order relative to the code that queued them
  2. the user can close the window mid-log; _on_close runs root.destroy(), and
     the outer callback resumes against destroyed widgets -> TclError
  3. the user can click Connect/Disconnect during a log write, re-entering
     _toggle_connection - which feeds F-14 directly

The recommended primitive is update_idletasks(), which flushes rendering without
dispatching user input or after callbacks - or nothing at all, since the
after-based design already returns control to the event loop naturally.
================================================================================
"""

REQUIRES_DISPLAY = True

import ast

import harness as h


def test_reentrancy(app):
    """Does a queued callback run *inside* _log, before the caller resumes?"""
    h.step("1. Does _log dispatch other pending callbacks mid-callback?")

    order = []

    def queued_later():
        order.append("queued-callback-ran")

    def outer_callback():
        order.append("outer-start")
        app.root.after(0, queued_later)   # queued while outer is on the stack
        app._log("a log line from inside an after callback")
        order.append("outer-end")

    app.root.after(0, outer_callback)
    app.root.update()
    h.pump(app.root, 0.2)

    h.detail("observed order", order)
    reentered = (order.index("queued-callback-ran") < order.index("outer-end")
                 if "queued-callback-ran" in order and "outer-end" in order
                 else False)
    h.detail("queued callback ran BEFORE the outer callback finished", reentered)
    if reentered:
        h.note("That is re-entrancy: _log ran unrelated queued work while its "
               "caller was still on the stack.")
    return reentered


def test_widget_destroyed_mid_log(app):
    """
    The window closed mid-log: does the outer callback resume against dead widgets?

    _on_close runs _save_current_settings then root.destroy() (app.py:504-507).
    Queueing destroy is exactly what clicking the window's X during a log write
    does, since update() will dispatch it.
    """
    h.step("2. Window closed mid-log - does the caller resume against dead widgets?")

    outcome = {}

    def outer_callback():
        # Standing in for the user clicking X while a log line is being written.
        app.root.after(0, app.root.destroy)
        try:
            app._log("log line that dispatches the queued destroy")
        except Exception as e:
            outcome["during_log"] = f"{type(e).__name__}: {e}"
        # The outer callback resumes here, against widgets that may be gone.
        try:
            app.log_text.insert("end", "work after the window was destroyed\n")
            outcome["after_resume"] = "no error"
        except Exception as e:
            outcome["after_resume"] = f"{type(e).__name__}: {e}"

    app.root.after(0, outer_callback)
    try:
        app.root.update()
    except Exception as e:
        outcome["update"] = f"{type(e).__name__}: {e}"

    h.detail("raised inside _log", outcome.get("during_log", "nothing"))
    h.detail("raised when the outer callback resumed",
             outcome.get("after_resume", "(callback did not resume)"))
    return outcome


def check_update_sites():
    """
    Find real update()/update_idletasks() calls in app.py.

    By AST, not by grep: the fix explains itself in a comment that names both
    methods, and a grep counted those comment lines as call sites - four
    "calls" where the source has two.
    """
    h.step("STATIC: where update() is called, and whether it should be")
    tree = ast.parse(h.source_text("app.py"))
    full_update, idle_only = [], []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            if node.func.attr == "update":
                full_update.append(node.lineno)
            elif node.func.attr == "update_idletasks":
                idle_only.append(node.lineno)
    for lineno in sorted(full_update):
        h.detail(f"app.py:{lineno}", "update()  <- dispatches the whole queue")
    for lineno in sorted(idle_only):
        h.detail(f"app.py:{lineno}", "update_idletasks()  <- redraw only")
    h.detail("full update() calls", len(full_update))
    h.detail("update_idletasks() calls", len(idle_only))
    return sorted(full_update)


def main():
    h.require_display()
    h.banner("F-15", "root.update() re-enters the event loop from callbacks")

    full_update = check_update_sites()

    with h.isolated_home(), h.temp_dir() as tmp:
        with h.tk_app(output_dir=tmp) as app:
            reentered = test_reentrancy(app)

        # A second, disposable app: this test destroys the root.
        with h.tk_app(output_dir=tmp) as app2:
            destroy_outcome = test_widget_destroyed_mid_log(app2)

    resumed_error = destroy_outcome.get("after_resume", "")
    tcl_error = "TclError" in resumed_error or "invalid command name" in resumed_error

    if reentered and full_update:
        detail = (f"; and with the window closed mid-log the caller resumes against "
                  f"destroyed widgets ({resumed_error})") if tcl_error else \
                 (f"; the destroy case resumed with: {resumed_error}")
        h.verdict("F-15", h.CONFIRMED,
                  f"_log's root.update() ({len(full_update)} full update() call sites "
                  f"in app.py) dispatches unrelated queued callbacks while its caller "
                  f"is still on the stack{detail}")
    elif not full_update:
        h.verdict("F-15", h.NOT_REPRODUCIBLE,
                  "no full update() calls remain in app.py")
    else:
        h.verdict("F-15", h.NOT_REPRODUCIBLE,
                  "queued callbacks did not run inside _log")


if __name__ == "__main__":
    main()
