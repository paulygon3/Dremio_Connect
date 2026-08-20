"""
================================================================================
F-11 - Every failed export leaves a corrupt .xlsx at the target path   (High)
================================================================================
pd.ExcelWriter.__exit__ calls close() -> save() unconditionally. It does not
inspect the exception state, so any error raised inside the `with` block still
triggers a save of the half-built workbook.

The artifact is the nasty part: a structurally valid zip containing only
metadata - no workbook.xml, no worksheet, no data. It lands at exactly the path
the success dialog would have named, and because the default filename pattern
carries a timestamp it does not overwrite anything, it accumulates.

The audit drove this through F-01's path (60 columns). That path no longer
fails - F-01 is fixed - so it moved to F-05's, a control byte openpyxl refuses.
F-05 is now fixed too, and the warning left here came true: the script reported
NOT REPRODUCIBLE because the export had stopped failing at all, not because the
artifact had stopped appearing. The verdict was vacuous rather than wrong, which
is the more dangerous of the two.

The trigger is now a timezone-aware timestamp. openpyxl raises

    ValueError: Excel does not support datetimes with timezones

from inside the write, which is what this script needs. It is also the least
incidental trigger of the three: Dremio serves TIMESTAMP WITH TIME ZONE columns,
so `SELECT some_ts_tz_column` reaches it without any contrivance.

**If that is ever handled too, this script needs another trigger.** The rule
holds generally - any exception raised inside the `with` block produces the same
artifact, so what matters is only that something still raises. The contract
check below now fails loudly when nothing does, instead of quietly passing.

(Noted in passing, not fixed here: the app has no handling for tz-aware
timestamps, so the user meets that raw openpyxl ValueError the way F-04 and F-05
used to be met. It is not one of the audit's 33 findings and is out of scope for
this item.)
================================================================================
"""

REQUIRES_DISPLAY = True

import gc
import zipfile

import pandas as pd

import harness as h

EXPECTED_MEMBERS = {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}


def main():
    h.require_display()
    h.banner("F-11", "Corrupt .xlsx left behind on every failed export")

    with h.isolated_home(), h.temp_dir() as tmp:
        h.step("Forcing an export failure with a timezone-aware timestamp")

        df = pd.DataFrame({
            "when": [pd.Timestamp("2024-01-01 12:00", tz="UTC")],
            "n": [1],
        })
        target = tmp / "failed_export.xlsx"

        def inspect(when):
            """Snapshot the artifact. Timing matters - see the note below."""
            if not target.exists():
                h.detail(when, "no file")
                return None
            size = target.stat().st_size
            is_zip = zipfile.is_zipfile(target)
            members = []
            if is_zip:
                with zipfile.ZipFile(target) as z:
                    members = z.namelist()
            h.detail(when, f"size={size} valid_zip={is_zip} members={members}")
            return {"size": size, "is_zip": is_zip, "members": members}

        with h.tk_app(output_dir=tmp, filename="failed_export.xlsx",
                      autofit=True) as app:
            app.df = df
            raised = None
            try:
                app._export_to_excel()
            except Exception as e:
                # Record the text, NOT the exception object. This mirrors
                # _execute_thread (app.py:734-737), which uses str(e) inside the
                # handler and lets Python delete `e` when the clause ends.
                # It matters: while the exception is alive its traceback pins the
                # frame of _export_to_excel, which pins the abandoned
                # ExcelWriter, so its zip central directory is never written and
                # the file on disk is a truncated non-zip. Once the handler ends
                # the writer is finalised and the file becomes the plausible
                # metadata-only zip the user actually finds.
                raised = f"{type(e).__name__}: {e}"

            h.detail("raised", raised or "nothing")

            h.step("What is on disk, while the app is still running")
            gc.collect()
            live = inspect("state the user sees")

        if live is None:
            h.verdict("F-11", h.NOT_REPRODUCIBLE,
                      "no file was left behind after the failed export")
            return

        size, is_zip, members = live["size"], live["is_zip"], live["members"]
        missing = EXPECTED_MEMBERS - set(members)
        h.detail("missing essential parts", sorted(missing) if missing else "none")

        h.step("Can anything open it, and does it contain the data?")
        rows_in_file = None
        try:
            back = pd.read_excel(target)
            openable = True
            rows_in_file = len(back)
            h.detail("openable by pandas", "YES")
            h.detail("rows in the source frame", len(df))
            h.detail("rows in the file", rows_in_file)
            h.detail("columns in the file", list(back.columns))
        except Exception as e:
            openable = False
            h.detail("openable by pandas", f"NO - {type(e).__name__}: {e}")

        complete = openable and rows_in_file == len(df)
        h.detail("file contains the exported data", complete)
        if openable and not complete:
            h.note("This is the more dangerous shape of the finding: the file "
                   "opens without complaint and carries the correct column "
                   "headers, but no data. A user has every reason to read that "
                   "as 'the query returned nothing' rather than 'the export "
                   "failed'.")

        h.step("Secondary observation: the artifact after the app tears down")
        after = inspect("after Tk teardown")
        if after and after != live:
            h.note("The abandoned writer is finalised a second time against an "
                   "already-closed handle, rewriting the file into something that "
                   "is not even a valid zip. Both states are reachable; the one "
                   "above is what is present while the app is still open, and is "
                   "what AUDIT.md measured.")

        h.step("Why the user cannot tell it apart from a real export")
        h.note(f"path is exactly what the success dialog would name: {target.name}")
        h.note("timestamped filenames mean these accumulate rather than overwrite")

    if raised is None:
        # Not NOT REPRODUCIBLE: nothing was tested. The export succeeding means
        # the trigger has been fixed, not that the artifact has stopped being
        # left behind - and reporting a pass here would retire a High finding on
        # the strength of a measurement that never happened.
        h.verdict("F-11", h.BLOCKED,
                  "the export did not fail, so the finding was not exercised at "
                  "all. The trigger has been fixed out from under this script "
                  "(it has already happened twice - see the module docstring). "
                  "Give it something that still raises inside the ExcelWriter "
                  "block before trusting any verdict here")
    elif raised is not None and is_zip and not openable and missing:
        h.verdict("F-11", h.CONFIRMED,
                  f"failed export left a {size}-byte structurally valid zip containing "
                  f"only metadata ({len(members)} members, missing {sorted(missing)}); "
                  f"not openable by pandas, Excel reports it as corrupt")
    elif openable and not complete:
        h.verdict("F-11", h.CONFIRMED,
                  f"failed export left a {size}-byte file that OPENS CLEANLY with the "
                  f"correct column headers and {rows_in_file} of {len(df)} rows. This "
                  f"is worse than the corrupt-zip case the audit recorded: nothing "
                  f"signals a problem, so it reads as an empty result set rather than "
                  f"a failed export")
    elif complete:
        h.verdict("F-11", h.NOT_REPRODUCIBLE,
                  f"a file was left behind but it is complete ({rows_in_file} rows) - "
                  f"unexpected after a failure; check the trigger")
    else:
        h.verdict("F-11", h.CONFIRMED,
                  f"failed export left an unusable {size}-byte artifact at the "
                  f"target path (valid_zip={is_zip})")


if __name__ == "__main__":
    main()
