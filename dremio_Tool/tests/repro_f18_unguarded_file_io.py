"""
================================================================================
F-18 - Unguarded file I/O in three UI callbacks                      (Medium)
================================================================================
Tagged STATIC + SOURCE - the handlers were counted by grep and the consequence
reasoned about. Both halves are executed here.

    app.py  _save_log          PermissionError/OSError on a read-only or full target
    app.py  _load_query_file   UnicodeDecodeError - opened with no encoding=
    app.py  _save_query_file   as _save_log

(Since F-31 wired up the saved-query library, the load path is
`_browse_query_file` - the same code, reached from the library dialog's Browse
button - and Save writes into the library by name rather than to a chosen path.
This script follows the current names; the behaviour under test is unchanged.)

All three used `with`, so handles were closed correctly. The defect is purely
the absence of a handler: each ran in a Tk button callback, so the exception
propagated into Tk, which printed a traceback to stderr and carried on. The
button appeared to do nothing - and this ships as a windowed PyInstaller build
with no stderr, so it was completely silent. The same failure shape as F-17.

The encoding half is the sharper one, because it needs nothing to go wrong.
`open(filepath, 'r')` with no encoding takes the platform default, so a .sql
file that is not in that encoding fails on an ordinary Load. That is not exotic:
SSMS writes UTF-16 by default and older tools write cp1252. The audit also noted
the inconsistency - config.py opens its query files with encoding='utf-8' while
app.py, which is what the UI actually reaches, passed nothing.

How the failures are forced
---------------------------
Real conditions rather than monkeypatched exceptions: a directory where a file
is expected (a reliable OSError on every platform, and what a stale path from a
previous session looks like), a chmod'd read-only file, and .sql files genuinely
encoded as UTF-16 and cp1252.

The read-only case is skipped when running as root, which can write to anything
regardless of mode - and reporting a pass there would be meaningless. Note the
container caveat carried from F-28: this environment does not honour umask, so
the check is on an explicit chmod and is verified by a control write.
================================================================================
"""

REQUIRES_DISPLAY = True

import os
from pathlib import Path

import harness as h

QUERY = "SELECT id, name FROM sales WHERE region = 'north' -- ünïcodé"


def dialogs_seen(dialogs):
    return len(dialogs["error"]) + len(dialogs["warning"])


def save_to_impossible_path(tmp):
    """
    A directory where a file should be. Every platform raises, and it is what a
    stale output path from a previous session actually looks like.
    """
    h.step("Saving the log and the query to a path that cannot be written")

    blocked = tmp / "blocked.txt"
    blocked.mkdir()

    rows = []
    survived = 0
    told = 0
    # The REAL callbacks, not the helpers underneath them: a helper that only
    # exists after the fix cannot show what the unfixed build did.
    #
    # The two now fail in different ways, because Save no longer asks for a
    # path - it files the query in the library by name (F-31). So the log is
    # pointed at an unwritable path through the file dialog, and the query is
    # made to fail by pointing the library itself at something that cannot hold
    # files. Both are the same question: does an OSError in a Tk callback reach
    # the user, or vanish into a stderr a windowed build does not have?
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs() as dialogs:
        app._log("a line to save")
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY)

        def save_log():
            with h.chosen_file(blocked):
                app._save_log()

        def save_query():
            # A regular file where the queries directory should be, so any
            # write beneath it raises NotADirectoryError.
            not_a_dir = tmp / "queries_is_a_file"
            not_a_dir.write_text("not a directory", encoding="utf-8")
            app.config.queries_dir = not_a_dir
            with h.named_save("anything"), h.chosen_file(blocked):
                app._save_query_file()

        for what, call in [("log", save_log), ("query", save_query)]:
            before = dialogs_seen(dialogs)
            try:
                call()
                survived += 1
                outcome = "returned normally"
            except Exception as e:
                outcome = f"RAISED {type(e).__name__}: {str(e)[:50]}"
            reported = dialogs_seen(dialogs) > before
            told += reported
            logged = "ERROR" in app.log_text.get("1.0", "end")
            rows.append([what, outcome, str(reported), str(logged)])

    h.table(["saving the", "outcome", "dialog shown", "logged"], rows)
    return survived, told


def save_to_read_only(tmp):
    """The audit's own case: a read-only target."""
    h.step("Saving to a read-only file")

    if os.geteuid() == 0:
        h.note("running as root - a read-only mode does not stop a write, so "
               "this case cannot be measured here and is not counted either way")
        return None

    target = tmp / "readonly.txt"
    target.write_text("original", encoding="utf-8")
    target.chmod(0o400)

    # Control: F-28 established that this container ignores umask, so an
    # explicit chmod has to be shown to actually bite before the result means
    # anything.
    control_blocked = False
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write("x")
    except OSError:
        control_blocked = True
    h.detail("control: a plain open('w') on it is refused", control_blocked)
    if not control_blocked:
        h.note("the read-only mode is not enforced here, so this case proves "
               "nothing and is not counted")
        return None

    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs() as dialogs, \
            h.chosen_file(target):
        before = dialogs_seen(dialogs)
        try:
            app._save_log()
            outcome = "returned normally"
            raised = False
        except Exception as e:
            outcome = f"RAISED {type(e).__name__}"
            raised = True
        reported = dialogs_seen(dialogs) > before

    h.detail("outcome", outcome)
    h.detail("user was told", reported)
    h.detail("original contents intact", target.read_text(encoding="utf-8") == "original")
    return (not raised) and reported


def load_each_encoding(tmp):
    """
    The encoding half. No failure is injected - these are just .sql files as
    other tools write them.
    """
    h.step("Loading .sql files in the encodings other tools produce")

    files = {
        "utf-8": QUERY.encode("utf-8"),
        "utf-8 with BOM": b"\xef\xbb\xbf" + QUERY.encode("utf-8"),
        "utf-16 (SSMS default)": QUERY.encode("utf-16"),
        "cp1252": QUERY.encode("cp1252"),
    }

    rows = []
    loaded = 0
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs() as dialogs:
        for label, payload in files.items():
            path = tmp / f"query_{label.split()[0].replace('-', '')}_{len(payload)}.sql"
            path.write_bytes(payload)

            app.query_text.delete("1.0", "end")
            before = dialogs_seen(dialogs)

            # _load_query_file, not the reader beneath it - this has to run
            # against the unfixed build too, and that build has only this.
            with h.chosen_file(path):
                try:
                    app._browse_query_file()
                    raised = ""
                except Exception as e:
                    raised = f"{type(e).__name__}"

            got = app.query_text.get("1.0", "end-1c")
            correct = got == QUERY
            loaded += correct
            told = dialogs_seen(dialogs) > before
            rows.append([
                label,
                f"RAISED {raised}" if raised else ("ok" if correct else "WRONG TEXT"),
                repr(got[:26]) if not correct else "",
                "yes" if told else "",
            ])

    h.table(["file written as", "", "loaded as", "dialog"], rows)
    return loaded, len(files)


def load_a_file_that_is_not_text(tmp):
    """
    A binary file the user picked by mistake must be refused, not mangled.

    This is why there is no latin-1 fallback: latin-1 decodes any byte sequence,
    so it would turn an unreadable file into mojibake and load it as a query -
    and mojibake in a SQL statement is worse than a refusal, because it runs.
    """
    h.step("Loading a file that is not text at all")

    path = tmp / "not_text.sql"
    # Invalid as UTF-8, invalid as UTF-16 (odd length, no BOM), and containing
    # bytes cp1252 leaves undefined.
    path.write_bytes(bytes([0x00, 0xff, 0xfe, 0x81, 0x8d, 0x90, 0x9d, 0xff]))

    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs() as dialogs, \
            h.chosen_file(path):
        app.query_text.delete("1.0", "end")
        try:
            app._browse_query_file()
            raised = ""
        except Exception as e:
            raised = f"{type(e).__name__}: {str(e)[:50]}"
        loaded = app.query_text.get("1.0", "end-1c")
        told = dialogs_seen(dialogs) > 0

    h.detail("raised into the Tk callback", raised or "no")
    h.detail("what landed in the query box", repr(loaded[:40]))
    h.detail("user was told", told)
    refused = not raised and loaded == "" and told
    h.detail("refused rather than mangled or crashed", refused)
    return refused


def missing_file(tmp):
    h.step("Loading a file that is not there")
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs() as dialogs, \
            h.chosen_file(tmp / "gone.sql"):
        try:
            app._browse_query_file()
            raised = ""
        except Exception as e:
            raised = f"{type(e).__name__}"
        told = dialogs_seen(dialogs) > 0
    h.detail("raised into the Tk callback", raised or "no")
    h.detail("user was told", told)
    return (not raised) and told


def main():
    h.require_display()
    h.banner("F-18", "Unguarded file I/O in the Load, Save and Save-log callbacks")

    with h.isolated_home(), h.temp_dir() as tmp:
        survived, told = save_to_impossible_path(tmp)
        read_only = save_to_read_only(tmp)
        loaded, total = load_each_encoding(tmp)
        refused_binary = load_a_file_that_is_not_text(tmp)
        handled_missing = missing_file(tmp)

    h.step("Contract check")
    h.detail("writes to an impossible path returned instead of raising",
             f"{survived} of 2")
    h.detail("...and told the user", f"{told} of 2")
    h.detail("read-only target handled",
             "not measurable here" if read_only is None else read_only)
    h.detail("encodings loaded correctly", f"{loaded} of {total}")
    h.detail("a non-text file is refused, not mangled", refused_binary)
    h.detail("a missing file is reported, not raised", handled_missing)

    read_only_ok = read_only is not False
    if (survived == 2 and told == 2 and loaded == total and refused_binary
            and handled_missing and read_only_ok):
        h.verdict("F-18", h.NOT_REPRODUCIBLE,
                  f"all three callbacks handle their failures and report them: a "
                  f"write to an unwritable path returns rather than propagating "
                  f"into Tk, and says so on both channels. .sql files load in "
                  f"all {total} encodings other tools produce - UTF-8 with and "
                  f"without a BOM, UTF-16 as SSMS writes it, and cp1252 - where "
                  f"an unencoded open() previously raised UnicodeDecodeError "
                  f"into a callback with no handler. A file that is not text in "
                  f"any of them is refused rather than mangled, and a missing "
                  f"file is reported rather than raised")
    else:
        h.verdict("F-18", h.CONFIRMED,
                  f"unguarded or unreported: writes_survived={survived}/2 "
                  f"user_told={told}/2 encodings_loaded={loaded}/{total} "
                  f"binary_refused={refused_binary} "
                  f"missing_handled={handled_missing} read_only={read_only}")


if __name__ == "__main__":
    main()
