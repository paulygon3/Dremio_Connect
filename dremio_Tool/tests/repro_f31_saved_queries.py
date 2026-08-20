"""
================================================================================
F-31 - The saved-queries subsystem was implemented and never called   (Low)
================================================================================
F-31 is a list of 17 unreferenced definitions. Most are hygiene; four are not.

config.py has always implemented a saved-query library over a saved_queries/
directory it creates on every start:

    get_saved_queries()      list the collection
    save_query_file(name, q) add to it
    load_query_file(path)    read one back
    delete_query_file(path)  remove one

The UI called none of them. Load and Save used their own filedialog instead, so
the app carried two implementations of one feature, shipped the one without a
library, and created an empty directory forever. AUDIT.md is explicit that this
is a decision rather than a defect: delete the orphans, or wire them up. The
choice made was to wire them up.

What that changes, and what it deliberately does not
----------------------------------------------------
The library is now the primary path: Save asks for a NAME and files the query in
saved_queries/, and Library lists what is there with Open and Delete.

Browse is kept. Opening a .sql file from anywhere was a documented capability,
and removing it to tidy the code up would be a regression dressed as a fix - so
the escape hatch lives inside the library dialog and still goes through the
encoding detection F-18 added.

Wiring a subsystem up makes its edges reachable for the first time, which is the
same lesson F-07 taught about sheet names. The sanitiser here was written when
nothing called it and had three reachable edges: a name of only illegal
characters collapsed to '' and produced the hidden file '.sql'; '..' survived to
produce '...sql'; and trailing spaces made 'report ' and 'report' two different
queries that look identical in a list. All three are checked below.
================================================================================
"""

REQUIRES_DISPLAY = True

import harness as h

QUERY_A = "SELECT id, name FROM sales WHERE region = 'north'"
QUERY_B = "SELECT COUNT(*) FROM audit_log"

# (label, name offered, expected stem or None if it must be refused)
NAME_CASES = [
    ("ordinary", "daily sales", "daily sales"),
    ("path separators stripped", "../../etc/passwd", "etcpasswd"),
    ("dots only", "..", None),
    ("illegal only", "///", None),
    ("empty", "", None),
    ("trailing space", "report ", "report"),
    ("leading dot", ".hidden", "hidden"),
]


def reset_library(app):
    """
    Empty the library before a section runs.

    Every section here shares one isolated HOME, so without this each starts
    with whatever the previous one saved - and an assertion about "what is in
    the library now" quietly becomes an assertion about test ordering.
    """
    for path in app.config.get_saved_queries():
        path.unlink()


def the_ui_calls_the_subsystem():
    """The finding itself: are the four methods reachable from app.py now?"""
    h.step("Are config.py's saved-query methods called from the UI at all?")
    rows = []
    called = 0
    for method in ("get_saved_queries", "save_query_file", "load_query_file",
                   "delete_query_file"):
        hits = h.grep_source(rf"config\.{method}\(", ["app.py"])
        if hits:
            called += 1
        rows.append([method,
                     "; ".join(f"app.py:{line}" for _, line, _ in hits)
                     or "NO CALLER"])
    h.table(["config.py method", "called from"], rows)
    return called


def save_and_list(tmp):
    h.step("Saving through the real handler, and listing it back")
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs():
        reset_library(app)
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY_A)

        with h.named_save("daily sales"):
            app._save_query_file()

        saved = app.config.get_saved_queries()
        h.detail("files in saved_queries/", [p.name for p in saved])
        h.detail("directory used", str(app.config.queries_dir))
        contents = saved[0].read_text(encoding="utf-8") if saved else ""
        h.detail("contents round-trip", contents == QUERY_A)
        return [p.name for p in saved], contents == QUERY_A


def name_edges(tmp):
    """Wiring it up is what makes the sanitiser's edges reachable."""
    h.step("Names the library is offered")
    rows = []
    wrong = []
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs() as dialogs:
        reset_library(app)
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY_B)

        for label, offered, expected_stem in NAME_CASES:
            before = {p.name for p in app.config.get_saved_queries()}
            errors_before = len(dialogs["error"])
            with h.named_save(offered):
                app._save_query_file()
            after = {p.name for p in app.config.get_saved_queries()}
            new = sorted(after - before)

            if expected_stem is None:
                ok = not new and len(dialogs["error"]) > errors_before
                outcome = "refused, and said so" if ok else (
                    f"WROTE {new}" if new else "refused silently")
            else:
                ok = new == [f"{expected_stem}.sql"]
                outcome = f"saved as {new[0]}" if new else "nothing written"

            if not ok:
                wrong.append(label)
            rows.append([label, repr(offered), outcome, "ok" if ok else "WRONG"])

    h.table(["case", "name offered", "what happened", ""], rows)
    return wrong


def load_and_delete(tmp):
    h.step("Opening and deleting through the library")
    state = {}
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs():
        reset_library(app)
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY_A)
        with h.named_save("first"):
            app._save_query_file()
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY_B)
        with h.named_save("second"):
            app._save_query_file()

        saved = {p.stem: p for p in app.config.get_saved_queries()}
        h.detail("library contains", sorted(saved))

        # Load 'first' back over an editor holding something else.
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", "-- scratch")
        loaded = app._library_load(saved["first"])
        state["loaded"] = loaded
        state["editor"] = app.query_text.get("1.0", "end-1c")
        h.detail("_library_load returned", loaded)
        h.detail("editor now holds the saved query",
                 state["editor"] == QUERY_A)

        # Delete 'second' - captured_dialogs answers yes by default.
        removed = app._library_delete(saved["second"])
        remaining = sorted(p.stem for p in app.config.get_saved_queries())
        state["deleted"] = removed
        state["remaining"] = remaining
        h.detail("_library_delete returned", removed)
        h.detail("library now contains", remaining)

    return state


def delete_is_confirmed(tmp):
    """Answering No must leave the file alone."""
    h.step("Declining the delete confirmation")
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs(answer_yes=False):
        reset_library(app)
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY_A)
        with h.named_save("keep me"):
            app._save_query_file()
        target = app.config.get_saved_queries()[0]
        removed = app._library_delete(target)
        still_there = target.exists()
    h.detail("_library_delete returned", removed)
    h.detail("file still present", still_there)
    return (not removed) and still_there


def overwrite_is_confirmed(tmp):
    """Saving over an existing name must ask, and No must keep the original."""
    h.step("Saving over a name that already exists")
    with h.tk_app(output_dir=tmp) as app, h.captured_dialogs(answer_yes=False) as dialogs:
        reset_library(app)
        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY_A)
        with h.named_save("clash"):
            app._save_query_file()
        original = app.config.get_saved_queries()[0].read_text(encoding="utf-8")

        app.query_text.delete("1.0", "end")
        app.query_text.insert("1.0", QUERY_B)
        asked_before = len(dialogs["askyesno"])
        with h.named_save("clash"):
            app._save_query_file()
        asked = len(dialogs["askyesno"]) > asked_before
        after = app.config.get_saved_queries()[0].read_text(encoding="utf-8")

    h.detail("user was asked before replacing", asked)
    h.detail("original kept when the answer was No", after == original)
    return asked and after == original


def main():
    h.require_display()
    h.banner("F-31", "The saved-query library, finally connected to the UI")

    called = the_ui_calls_the_subsystem()

    if called < 4:
        # Nothing to exercise. The behavioural sections below drive handlers
        # that only exist once the subsystem is connected, so running them
        # against an unwired build reports an AttributeError instead of the
        # finding. The finding is the count above.
        h.note("The UI does not call the subsystem, so there is no library "
               "behaviour to measure - the sections below are skipped.")
        h.verdict("F-31", h.CONFIRMED,
                  f"only {called} of config.py's 4 saved-query methods have a "
                  f"caller in app.py - the subsystem is still orphaned, the UI "
                  f"still uses its own filedialog, and saved_queries/ is created "
                  f"on every start and never used")
        return

    with h.isolated_home(), h.temp_dir() as tmp:
        saved_names, round_tripped = save_and_list(tmp)
        wrong_names = name_edges(tmp)
        state = load_and_delete(tmp)
        delete_confirmed = delete_is_confirmed(tmp)
        overwrite_confirmed = overwrite_is_confirmed(tmp)

    loaded_ok = state.get("loaded") and state.get("editor") == QUERY_A
    deleted_ok = state.get("deleted") and state.get("remaining") == ["first"]

    h.step("Contract check")
    h.detail("config methods now called from app.py", f"{called} of 4")
    h.detail("save writes into the library", saved_names)
    h.detail("contents round-trip", round_tripped)
    h.detail("name cases wrong", wrong_names or "none")
    h.detail("open loads the saved query", loaded_ok)
    h.detail("delete removes it", deleted_ok)
    h.detail("delete asks first, and No keeps the file", delete_confirmed)
    h.detail("overwrite asks first, and No keeps the original",
             overwrite_confirmed)

    if (called == 4 and round_tripped and not wrong_names and loaded_ok
            and deleted_ok and delete_confirmed and overwrite_confirmed):
        h.verdict("F-31", h.NOT_REPRODUCIBLE,
                  "the saved-queries subsystem is connected: all four config.py "
                  "methods are called from the UI, Save files the query by name "
                  "in saved_queries/ instead of scattering it, and the library "
                  "dialog opens and deletes from that collection. The directory "
                  "the app has always created is finally the thing it is for. "
                  "Both destructive paths ask first and honour No, and the "
                  "sanitiser edges that wiring it up made reachable - a name "
                  "that reduces to nothing, '..', trailing spaces - are refused "
                  "or normalised rather than producing '.sql' and lookalike "
                  "entries")
    elif called < 4:
        h.verdict("F-31", h.CONFIRMED,
                  f"only {called} of config.py's 4 saved-query methods have a "
                  f"caller in app.py - the subsystem is still orphaned and the "
                  f"UI still uses its own filedialog")
    else:
        h.verdict("F-31", h.CONFIRMED,
                  f"wired up but not behaving: round_tripped={round_tripped} "
                  f"bad_names={wrong_names} loaded={loaded_ok} "
                  f"deleted={deleted_ok} delete_confirmed={delete_confirmed} "
                  f"overwrite_confirmed={overwrite_confirmed}")


if __name__ == "__main__":
    main()
