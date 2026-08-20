"""
================================================================================
F-25 - Filename pattern without {timestamp} silently overwrites   (High)
================================================================================
generate_timestamp_filename (utils.py) is a plain str.replace, and the Filename
field was a free-text ttk.Entry with no validation whatsoever.

The reachable case is the ordinary one: typing `report.xlsx` - the single most
natural thing to type - made every subsequent export silently overwrite the
previous one. pd.ExcelWriter opens in write mode with no existence check and no
prompt, and the success dialog reported a normal export either way.

The agreed fix has four parts, and this script checks each:

  1. reject an empty filename
  2. enforce an extension
  3. block path traversal
  4. prompt before overwriting an existing file

Part 4 is checked through the real _execute_and_export, because the prompt has
to happen on the main thread - a worker cannot ask the user anything - and its
placement is the substance of the fix.
================================================================================
"""

REQUIRES_DISPLAY = True

import threading
from pathlib import Path

import pandas as pd

import harness as h

PATTERNS = [
    ("dremio_export_{timestamp}.xlsx", "the default - substitutes"),
    ("report.xlsx", "fixed name - the overwrite case"),
    ("{TIMESTAMP}.xlsx", "wrong case - does not substitute"),
    ("noext", "no extension"),
    ("", "empty"),
    ("../escaped.xlsx", "traversal"),
    ("sub/dir/report.xlsx", "path, not a name"),
    ('bad:name?.xlsx', "characters Windows rejects"),
]


# Shared, so it stays in step with DremioConnection's signature.
StubConnection = h.StubConnection


def validation_table():
    h.step("Filename validation (real utils.validate_output_filename)")
    h.add_src_to_path()
    from utils import validate_output_filename, generate_timestamp_filename

    rows = []
    accepted = {}
    for pattern, description in PATTERNS:
        ok, error, normalised = validate_output_filename(pattern)
        accepted[pattern] = ok
        if ok:
            result = repr(generate_timestamp_filename(normalised))
        else:
            result = f"rejected: {error.splitlines()[0]}"
        rows.append([repr(pattern), description, "accept" if ok else "REJECT", result])
    h.table(["pattern", "case", "", "result"], rows)
    return accepted


def export_twice(tmp, answer_yes):
    """
    Two real Execute runs to the same fixed filename.

    Returns (prompted, files, second_contents).
    """
    morning = pd.DataFrame({"extract": ["morning"], "rows": [111]})
    afternoon = pd.DataFrame({"extract": ["afternoon"], "rows": [222]})
    out = tmp / ("yes" if answer_yes else "no")
    out.mkdir(parents=True, exist_ok=True)

    state = {}
    with h.tk_app(output_dir=out, filename="report.xlsx", autofit=False,
                  open_after=False) as app, \
            h.captured_dialogs(answer_yes=answer_yes) as dialogs:

        def run(df):
            app.connection = StubConnection(df)
            app.query_text.delete("1.0", "end")
            app.query_text.insert("1.0", "SELECT 1")
            before = set(threading.enumerate())
            app._execute_and_export()
            worker = h.new_threads_from(before)
            if worker:
                h.wait_for(app.root,
                           lambda: not worker[0].is_alive(), timeout=30)
            h.pump(app.root, 0.3)

        def scenario():
            run(morning)
            state["prompted_first"] = len(dialogs["askyesno"])
            run(afternoon)
            state["prompted_second"] = len(dialogs["askyesno"])
            state["prompt_text"] = (dialogs["askyesno"][-1][1]
                                    if dialogs["askyesno"] else None)

        h.run_with_mainloop(app.root, scenario)

    files = sorted(p.name for p in out.glob("*.xlsx"))
    contents = pd.read_excel(out / "report.xlsx").to_dict("records") \
        if (out / "report.xlsx").exists() else None
    return state, files, contents


def main():
    h.require_display()
    h.banner("F-25", "Filename validation and silent overwrite")

    with h.isolated_home(), h.temp_dir() as tmp:
        accepted = validation_table()

        h.step("Overwrite prompt: second export, user answers YES")
        state_yes, files_yes, contents_yes = export_twice(tmp, answer_yes=True)
        h.detail("prompts after the first export", state_yes.get("prompted_first"))
        h.detail("prompts after the second export", state_yes.get("prompted_second"))
        if state_yes.get("prompt_text"):
            h.detail("prompt text",
                     state_yes["prompt_text"].replace("\n", " | ")[:120])
        h.detail("files in the output folder", files_yes)
        h.detail("contents after overwrite", contents_yes)

        h.step("Overwrite prompt: second export, user answers NO")
        state_no, files_no, contents_no = export_twice(tmp, answer_yes=False)
        h.detail("prompts after the second export", state_no.get("prompted_second"))
        h.detail("files in the output folder", files_no)
        h.detail("contents preserved", contents_no)

    empty_rejected = not accepted.get("")
    traversal_rejected = not accepted.get("../escaped.xlsx")
    path_rejected = not accepted.get("sub/dir/report.xlsx")
    extension_enforced = accepted.get("noext")   # accepted, with .xlsx appended
    prompted = state_yes.get("prompted_second") == 1
    cancel_preserves = contents_no == [{"extract": "morning", "rows": 111}]

    h.step("Contract check")
    h.detail("1. empty filename rejected", empty_rejected)
    h.detail("2. extension enforced (appended, not rejected)", extension_enforced)
    h.detail("3. path traversal blocked", traversal_rejected and path_rejected)
    h.detail("4. prompted before overwriting", prompted)
    h.detail("   answering No preserves the original", cancel_preserves)

    if (empty_rejected and traversal_rejected and path_rejected
            and extension_enforced and prompted and cancel_preserves):
        h.verdict("F-25", h.NOT_REPRODUCIBLE,
                  "empty filenames and path traversal are rejected, a missing "
                  "extension is normalised to .xlsx, and a second export to the "
                  "same name now prompts before replacing it - answering No leaves "
                  "the original file intact")
    elif not prompted:
        h.verdict("F-25", h.CONFIRMED,
                  f"a second export to the same filename still overwrote without "
                  f"prompting (files={files_yes})")
    else:
        h.verdict("F-25", h.CONFIRMED,
                  f"validation incomplete: empty_rejected={empty_rejected} "
                  f"traversal={traversal_rejected} path={path_rejected} "
                  f"extension={extension_enforced} cancel_preserves={cancel_preserves}")


if __name__ == "__main__":
    main()
