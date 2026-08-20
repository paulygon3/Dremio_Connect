"""
================================================================================
F-17 - _open_output_folder raises into the Tk callback with no handler  (Medium)
================================================================================
SOURCE-only in AUDIT.md. The same os.startfile call as F-16, but this time on
the main thread inside a button callback with no try (app.py:568-574).

Tk catches exceptions raised by callbacks, prints a traceback to stderr, and
continues. So the button appears to do nothing at all - no dialog, no log line,
no state change. In the documented PyInstaller windowed build (README.md:215)
there is no stderr either, so the failure is completely silent.

The button is exercised through Tcl via .invoke() rather than by calling the
method directly, because the whole finding is about what Tk's callback machinery
does with the exception. Calling the bound method from Python would let the
exception propagate to the caller and prove nothing.
================================================================================
"""

REQUIRES_DISPLAY = True

import os
import tkinter as tk

import harness as h


def main():
    h.require_display()
    h.banner("F-17", "Open Output Folder fails silently")

    h.step("STATIC: is the call guarded?")
    # Located by AST rather than by line range: the ranges in the original
    # audit went stale the moment anything above this method moved.
    import ast
    tree = ast.parse(h.source_text("app.py"))
    method = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_open_output_folder"), None)
    guarded = []
    startfile_calls = 0
    if method:
        h.detail("_open_output_folder found at", f"app.py:{method.lineno}")
        for node in ast.walk(method):
            if isinstance(node, (ast.Try, ast.ExceptHandler)):
                guarded.append(f"app.py:{node.lineno}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startfile"):
                startfile_calls += 1
        h.detail("os.startfile calls in the method", startfile_calls)
    h.detail("try/except inside _open_output_folder", guarded or "NONE")

    with h.isolated_home(), h.temp_dir() as tmp:
        # captured_dialogs is mandatory here now: once the callback is guarded
        # it reports failures with a real messagebox, which would block forever.
        with h.tk_app(output_dir=tmp) as app, h.captured_dialogs() as dialogs:
            h.detail("output folder exists (so the else branch is not taken)",
                     tmp.exists())

            # Record what Tk does with the exception rather than letting it
            # print to the real stderr.
            reported = []
            app.root.report_callback_exception = \
                lambda exc, val, tb: reported.append(f"{exc.__name__}: {val}")

            h.step("Clicking the button through Tcl (.invoke), as a user would")
            button = tk.Button(app.root, text="Open Output Folder",
                               command=app._open_output_folder)
            escaped = None
            try:
                button.invoke()
            except Exception as e:
                escaped = f"{type(e).__name__}: {e}"

            h.detail("exception escaped to the caller", escaped or "no")
            h.detail("exception caught by Tk's callback machinery",
                     reported or "none")

            h.step("What the user observes")
            log_text = app.log_text.get("1.0", "end")
            log_lines = [ln.strip() for ln in log_text.splitlines()
                         if "Windows only" in ln or "ERROR" in ln
                         or "folder" in ln.lower()]
            h.detail("log lines added by the click", log_lines or "NONE")
            shown = (dialogs["info"] + dialogs["error"] + dialogs["warning"])
            h.detail("dialogs shown", len(shown))
            for title, message in shown:
                h.detail(f"  dialog [{title}]", message.replace("\n", " | "))
            if not shown:
                h.note("Tk's default report_callback_exception prints the traceback "
                       "to stderr. A PyInstaller windowed build has no stderr, so "
                       "nothing at all reaches the user.")
            informed = bool(shown) or bool(log_lines)

    caught = [r for r in reported if "AttributeError" in r]
    has_startfile = hasattr(os, "startfile")

    if has_startfile:
        h.verdict("F-17", h.BLOCKED,
                  "os.startfile exists on this platform (Windows), so the "
                  "non-Windows failure path cannot be exercised here")
    elif caught and not informed:
        h.verdict("F-17", h.CONFIRMED,
                  f"the button callback raises {caught[0]} with no handler in "
                  f"_open_output_folder; Tk swallows it into "
                  f"report_callback_exception, so the button silently does nothing - "
                  f"no dialog, no log line, and in a windowed build no stderr either")
    elif informed and not caught:
        h.verdict("F-17", h.NOT_REPRODUCIBLE,
                  f"the callback no longer escapes to Tk's exception reporter, and "
                  f"the user is told what happened on both channels "
                  f"(dialogs and log line)")
    else:
        h.verdict("F-17", h.CONFIRMED,
                  f"partially handled: exception still reached Tk "
                  f"(reported={reported}) even though the user was informed "
                  f"({informed})")


if __name__ == "__main__":
    main()
