"""
================================================================================
F-06 - Duplicate column names crash the auto-fit loop   (High)
================================================================================
Reachable from `SELECT a.id, b.id FROM a JOIN b ON ...` - one of the most
ordinary queries there is - because Dremio returns both columns named `id` and
auto-fit is on by default.

The isolating control matters as much as the failure: with auto-fit OFF the
same frame exports cleanly, which places the fault in the app's auto-fit loop
(app.py:761-768) rather than in pandas' Excel writer. Both are run here.

Mechanism: for a duplicated label, self.df[col] returns a DataFrame rather than
a Series, so .astype(str).map(len).max() yields a Series of per-column maxima
and max(Series, int) raises.
================================================================================
"""

REQUIRES_DISPLAY = True

import pandas as pd

import harness as h


def duplicate_frame():
    """A frame with two columns literally named 'id', as a JOIN would produce."""
    df = pd.DataFrame([[1, "left", 2]], columns=["id", "name", "id"])
    return df


def attempt(tmp, autofit, filename):
    df = duplicate_frame()
    with h.tk_app(output_dir=tmp, filename=filename, autofit=autofit) as app:
        app.df = df
        try:
            path = app._export_to_excel()
            return None, path
        except Exception as e:
            return e, None


def main():
    h.require_display()
    h.banner("F-06", "Duplicate column names crash the auto-fit loop")

    with h.isolated_home(), h.temp_dir() as tmp:
        df = duplicate_frame()
        h.step("The frame under test")
        h.detail("columns", list(df.columns))
        h.detail("df['id'] returns", type(df["id"]).__name__)
        h.detail("shape", df.shape)

        h.step("Test: auto-fit ON (the default, app.py:301)")
        exc_on, _ = attempt(tmp, True, "dup_autofit_on.xlsx")
        if exc_on:
            h.detail("raised", f"{type(exc_on).__name__}: {exc_on}")
        else:
            h.detail("result", "exported without error")

        h.step("Control: auto-fit OFF - isolates the fault to the auto-fit loop")
        exc_off, path_off = attempt(tmp, False, "dup_autofit_off.xlsx")
        if exc_off:
            h.detail("raised", f"{type(exc_off).__name__}: {exc_off}")
        else:
            back = pd.read_excel(path_off, sheet_name=0)   # first sheet, whatever it is named
            h.detail("result", f"exported cleanly, shape {back.shape}")

    if exc_on is not None and exc_off is None:
        h.verdict("F-06", h.CONFIRMED,
                  f"auto-fit ON raises {type(exc_on).__name__}: {exc_on}; "
                  f"auto-fit OFF exports the same frame cleanly, so the fault is "
                  f"in the app's auto-fit loop, not in pandas")
    elif exc_on is not None:
        h.verdict("F-06", h.CONFIRMED,
                  f"auto-fit ON raises {type(exc_on).__name__}; note the control also "
                  f"failed ({type(exc_off).__name__}), so the fault is not auto-fit-only")
    else:
        h.verdict("F-06", h.NOT_REPRODUCIBLE,
                  "duplicate column names exported without error")


if __name__ == "__main__":
    main()
