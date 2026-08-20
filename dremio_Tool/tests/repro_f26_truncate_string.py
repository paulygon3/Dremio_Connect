"""
================================================================================
F-26 - truncate_string produces output longer than max_length          (Low)
================================================================================
    return text[:max_length - len(suffix)] + suffix

When max_length is smaller than the suffix, that bound goes negative and slices
from the END, so the function returns a string LONGER than the limit it was
asked to enforce:

    truncate_string('abcdefghij', max_length=2) -> 'abcdefghi...'   (12 chars)
    truncate_string('abcdefghij', max_length=0) -> 'abcdefg...'     (10 chars)

The same shape as F-01: correct across the first range, silently wrong past a
boundary, no error raised. Latent rather than live - the function has no
callers, and config.py reimplemented the logic inline instead of calling it
(F-27) - so the fix defuses a trap for whoever wires it up.

Checked as a property rather than a table
-----------------------------------------
The audit's four sample rows would pass against several wrong implementations,
including one that clamps at zero and still overshoots elsewhere. What the
function actually promises is one sentence - the result is never longer than
max_length - so that is asserted across the whole range, for inputs shorter
than, equal to, and longer than the bound, and for several suffixes.

The one case deliberately pinned to a value rather than a property is
max_length == len(suffix). That was already inside the bound and the audit
verified it, so it must keep returning the bare suffix: fixing the broken range
is not licence to change the range that worked.
================================================================================
"""

REQUIRES_DISPLAY = False

import harness as h

TEXT = "abcdefghij"
SUFFIXES = ["...", "…", "", " [cut]"]

# The audit's own table, kept as a regression check on the documented cases.
AUDIT_CASES = [
    (5, "ab...", 5),
    (3, "...", 3),
    (2, None, 2),   # was 'abcdefghi...' (12) - only the bound is pinned
    (0, None, 0),   # was 'abcdefg...'   (10)
]


def audit_table():
    h.step("The audit's cases")
    h.add_src_to_path()
    from utils import truncate_string

    rows = []
    over = []
    for max_length, expected, bound in AUDIT_CASES:
        got = truncate_string(TEXT, max_length=max_length)
        within = len(got) <= bound
        if not within:
            over.append((max_length, got, len(got)))
        matches = "-" if expected is None else ("ok" if got == expected else "CHANGED")
        rows.append([str(max_length), repr(got), str(len(got)),
                     "ok" if within else f"OVER by {len(got) - bound}", matches])
    h.table(["max_length", "result", "len", "within bound", "vs audit"], rows)
    return over


def bound_is_a_property():
    """
    The promise is 'never longer than max_length'. Assert it everywhere.
    """
    h.step("Is the bound respected across the whole range?")
    h.add_src_to_path()
    from utils import truncate_string

    violations = []
    checked = 0
    for suffix in SUFFIXES:
        for max_length in range(-3, 40):
            for text in ("", "a", TEXT, "x" * 100):
                got = truncate_string(text, max_length=max_length, suffix=suffix)
                checked += 1
                if len(got) > max(max_length, 0):
                    violations.append(
                        (repr(text[:12]), max_length, repr(suffix),
                         repr(got), len(got))
                    )

    h.detail("combinations checked", f"{checked:,}")
    h.detail("results longer than max_length", violations[:5] or "none")
    if len(violations) > 5:
        h.detail("...and more", f"{len(violations) - 5:,}")
    return violations


def short_text_untouched():
    """A string already inside the bound must come back unchanged."""
    h.step("Text shorter than the bound is returned as-is")
    h.add_src_to_path()
    from utils import truncate_string

    rows = []
    wrong = 0
    for text, max_length in [("abc", 10), ("abc", 3), ("", 5), ("abcdefghij", 10)]:
        got = truncate_string(text, max_length=max_length)
        ok = got == text
        wrong += not ok
        rows.append([repr(text), str(max_length), repr(got),
                     "ok" if ok else "MODIFIED"])
    h.table(["text", "max_length", "result", ""], rows)
    return wrong


def suffix_equal_boundary():
    h.step("max_length == len(suffix) keeps its audited behaviour")
    h.add_src_to_path()
    from utils import truncate_string

    got = truncate_string(TEXT, max_length=3, suffix="...")
    h.detail("truncate_string('abcdefghij', 3, '...')", repr(got))
    return got == "..."


def main():
    h.banner("F-26", "truncate_string and the bound it was asked to enforce")

    over = audit_table()
    violations = bound_is_a_property()
    modified = short_text_untouched()
    boundary_kept = suffix_equal_boundary()

    h.step("Contract check")
    h.detail("audit cases exceeding the bound", over or "none")
    h.detail("property violations across the range", len(violations))
    h.detail("short strings modified", modified)
    h.detail("max_length == len(suffix) still returns the suffix", boundary_kept)

    if not over and not violations and not modified and boundary_kept:
        h.verdict("F-26", h.NOT_REPRODUCIBLE,
                  "truncate_string never returns more than max_length: the "
                  "negative slice bound is gone, a max_length below the suffix "
                  "length now truncates plainly instead of slicing from the end, "
                  "and max_length <= 0 returns ''. Strings already inside the "
                  "bound are untouched, and max_length == len(suffix) still "
                  "returns the bare suffix as the audit recorded")
    else:
        h.verdict("F-26", h.CONFIRMED,
                  f"the bound is still not enforced: {len(violations)} of the "
                  f"checked combinations returned more than max_length "
                  f"(examples: {violations[:3]}), audit cases over the bound: "
                  f"{over}")


if __name__ == "__main__":
    main()
