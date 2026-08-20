"""
================================================================================
F-33 - NEW - Error dialogs raise NameError instead of displaying   (High)
================================================================================
Not in the original AUDIT.md. Found while reproducing F-14: the disconnect race
fired exactly as predicted, but the error dialog the audit says the user is
shown never appeared. The dialog is not merely unhelpful - it does not run at
all.

Mechanism. Both worker threads report failures like this:

    except Exception as e:                                        app.py:734
        self.root.after(0, lambda: self._log(f"ERROR: {str(e)}"))
        self.root.after(0, lambda: self.progress_label.config(text="Error"))
        self.root.after(0, lambda: messagebox.showerror("Error", str(e)))

Python deletes the name `e` when the except block ends - that is specified
behaviour, to break the exception's reference cycle. The lambdas capture `e` as
a free variable and run LATER, when the event loop dispatches them. By then the
name is unbound, and each lambda raises:

    NameError: cannot access free variable 'e' where it is not associated
               with a value in enclosing scope

Tk catches that, prints a traceback to stderr, and carries on. In the documented
PyInstaller windowed build (README.md:215) there is no stderr, so a failed query
or a failed connection produces NO dialog, NO log line, and NO console output.
The app simply appears to do nothing.

It is timing-dependent in an interesting way, and F-15 is why. The callbacks are
queued from a worker thread while the main thread is in mainloop, so some may be
dispatched BEFORE the except block ends, while `e` is still bound. Those
succeed. Any dispatched afterwards fail. The messagebox call is queued last, so
it is the most likely to fail - and it is the only one the user can see.

Both handlers are affected:
    app.py:671-675   _connect_thread   - _log and showerror capture `e`
    app.py:734-737   _execute_thread   - _log and showerror capture `e`
================================================================================
"""

REQUIRES_DISPLAY = True

import ast
import threading

import pandas as pd

import harness as h

TRIALS = 5


def find_late_bound_lambdas():
    """
    Find `root.after(..., lambda: ... e ...)` inside `except ... as e:` blocks.

    Reported by AST rather than grep so the binding is actually checked: the
    defect is specifically a lambda referencing the except-clause name, deferred
    past the end of the block.
    """
    tree = ast.parse(h.source_text("app.py"))
    findings = []
    for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
        if not handler.name:
            continue
        bound = handler.name
        for node in ast.walk(handler):
            if not isinstance(node, ast.Lambda):
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if bound in names:
                findings.append((node.lineno, bound))
    return sorted(set(findings))


def demonstrate(dispatch_during_handler):
    """
    Reproduce the pattern in isolation and report which callbacks survive.

    dispatch_during_handler stands in for the event loop servicing the queue
    while the except block is still running - which is what happens in the real
    app, because the callbacks are queued from a worker thread.
    """
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    succeeded, raised = [], []
    root.report_callback_exception = lambda t, v, tb: raised.append(f"{t.__name__}: {v}")

    def handler():
        try:
            raise ValueError("'NoneType' object has no attribute 'do_get'")
        except Exception as e:
            root.after(0, lambda: succeeded.append(("_log", str(e))))
            if dispatch_during_handler:
                root.update()
            root.after(0, lambda: succeeded.append(("showerror", str(e))))
        root.after(50, root.quit)
        root.mainloop()

    handler()
    root.destroy()
    return succeeded, raised


# A connected DremioConnection that yields a chosen frame, or raises.
#
# The dialog-delivery question is about _execute_thread's handler, which is
# identical no matter where the exception came from - so a stub transport keeps
# the measurement fast and lets each finding's real failure be triggered
# deterministically. Shared via the harness so its signature tracks the real
# DremioConnection; a private copy silently became a TypeError when
# execute_query gained cancel_event.
StubConnection = h.StubConnection


def _wide_df():        # F-01: column letters past 52
    return pd.DataFrame({f"c{i}": [i] for i in range(60)})


def _illegal_char_df():  # F-05: control character openpyxl rejects
    return pd.DataFrame({"payload": ["a\x00b"]})


def _duplicate_df():   # F-06: duplicate column names
    return pd.DataFrame([[1, "left", 2]], columns=["id", "name", "id"])


def _plain_df():       # F-25: valid frame, invalid filename
    return pd.DataFrame({"a": [1]})


# (label, description, frame factory, export kwargs, exception to raise instead)
DELIVERY_CASES = [
    ("F-01", "60 columns -> column-letter ValueError",
     _wide_df, {"autofit": True, "filename": "x.xlsx"}, None),
    ("F-05", "control byte -> IllegalCharacterError",
     _illegal_char_df, {"autofit": False, "filename": "x.xlsx"}, None),
    ("F-06", "duplicate column names -> Series ambiguity",
     _duplicate_df, {"autofit": True, "filename": "x.xlsx"}, None),
    ("F-25", "empty filename -> IsADirectoryError",
     _plain_df, {"autofit": False, "filename": ""}, None),
    ("F-14", "query itself fails (disconnect race shape)",
     None, {"autofit": False, "filename": "x.xlsx"},
     AttributeError("'NoneType' object has no attribute 'do_get'")),
]


def _run_failure_once(tmp, factory, kwargs, raise_exc):
    """Drive one real failure through _execute_and_export; report what surfaced."""
    with h.tk_app(output_dir=tmp, open_after=False, **kwargs) as app, \
            h.captured_dialogs() as dialogs:
        app.connection = StubConnection(
            factory() if factory else None, raise_exc)
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", "SELECT 1")

        state = {}

        def scenario():
            before = set(threading.enumerate())
            app._execute_and_export()
            worker = h.new_threads_from(before)
            h.wait_for(app.root,
                       lambda: worker and not worker[0].is_alive(), timeout=30)
            h.pump(app.root, 0.4)
            state["dialog"] = len(dialogs["error"]) > 0
            state["log"] = "ERROR" in app.log_text.get("1.0", "end")
            state["progress"] = str(app.progress_label["text"])

        h.run_with_mainloop(app.root, scenario)
        return state


def measure_dialog_delivery():
    """
    How often does the error dialog actually reach the user, per failure path?

    This is the sweep across every finding whose stated user-visible consequence
    is "a confusing dialog". All of them are delivered by the same lambda at
    app.py:737, so all of them inherit this defect.
    """
    h.step(f"Delivery rate across the real error paths ({TRIALS} trials each)")

    rows = []
    summary = {}
    with h.isolated_home(), h.temp_dir() as tmp:
        for label, description, factory, kwargs, raise_exc in DELIVERY_CASES:
            dialogs = logs = 0
            progress = ""
            for _ in range(TRIALS):
                state = _run_failure_once(tmp, factory, kwargs, raise_exc)
                dialogs += 1 if state.get("dialog") else 0
                logs += 1 if state.get("log") else 0
                progress = state.get("progress", "")
            rows.append([label, description,
                         f"{dialogs}/{TRIALS}", f"{logs}/{TRIALS}", repr(progress)])
            summary[label] = dialogs
    h.table(["finding", "failure path", "dialog shown", "log line", "progress label"],
            rows)
    return summary


def main():
    h.require_display()
    h.banner("F-33", "Error dialogs raise NameError instead of displaying (NEW)")

    h.step("STATIC: lambdas capturing an except-clause name, deferred via after()")
    sites = find_late_bound_lambdas()
    for lineno, bound in sites:
        h.detail(f"app.py:{lineno}", f"lambda captures `{bound}` from its except clause")
    if not sites:
        h.detail("late-bound exception lambdas", "NONE")

    h.step("Which handlers do they belong to?")
    for name, lineno, line in h.grep_source(
            r"messagebox\.showerror|_log\(f\"ERROR", ["app.py"]):
        h.detail(f"{name}:{lineno}", line)

    h.step("The pattern in isolation (a local reconstruction, NOT the app) - "
           "case 1: nothing dispatched during the handler")
    ok_a, err_a = demonstrate(False)
    h.detail("callbacks that ran", ok_a or "NONE")
    h.detail("callbacks that raised", err_a or "none")
    h.note("Neither the log line nor the dialog reaches the user.")

    h.step("The pattern in isolation - case 2: the queue is serviced mid-handler "
           "(what F-15's update() causes)")
    ok_b, err_b = demonstrate(True)
    h.detail("callbacks that ran", ok_b or "NONE")
    h.detail("callbacks that raised", err_b or "none")
    h.note("The earlier callback survives; the one queued last - the dialog - "
           "still fails.")

    h.step("Where the traceback goes")
    h.note("Tk catches it and prints to stderr. In the documented PyInstaller "
           "windowed build (README.md:215) there is no stderr, so the failure is "
           "completely invisible.")

    delivery = measure_dialog_delivery()
    undelivered = [f for f, count in delivery.items() if count < TRIALS]
    never = [f for f, count in delivery.items() if count == 0]

    h.step("Which findings this changes")
    h.note("Every finding whose stated consequence is 'the user sees a dialog' is "
           "delivered by the same lambda, so every one of them inherits this.")
    for label, count in sorted(delivery.items()):
        h.detail(label, f"dialog reached the user {count}/{TRIALS} times")

    all_raised = err_a + err_b
    nameerrors = [e for e in all_raised if e.startswith("NameError")]

    if sites and nameerrors:
        h.verdict("F-33", h.CONFIRMED,
                  f"{len(sites)} lambdas at "
                  f"{['app.py:%d' % n for n, _ in sites]} capture their except-clause "
                  f"name and are deferred via root.after past the end of the block; "
                  f"they raise NameError when dispatched. Measured across the real "
                  f"failure paths, the error dialog fails to reach the user for "
                  f"{sorted(undelivered)} (never at all for {sorted(never)}) over "
                  f"{TRIALS} trials each. Every failure path in _connect_thread and "
                  f"_execute_thread is affected")
    elif not sites:
        h.verdict("F-33", h.NOT_REPRODUCIBLE,
                  "no lambda captures an except-clause name any more")
    else:
        h.verdict("F-33", h.NOT_REPRODUCIBLE,
                  "the deferred lambdas did not raise NameError")


if __name__ == "__main__":
    main()
