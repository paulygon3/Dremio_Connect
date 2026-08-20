"""
================================================================================
F-27 - History labels collide, making the dropdown ambiguous   (Low)
================================================================================
Tagged EXECUTED + SOURCE. The collision itself was executed; the SOURCE half is
the Tk semantics - the claim that ttk.Combobox.current() still returns the right
index despite duplicate display strings came from reading Tk's behaviour, not
from running it. A display is available now, so it is run here.

The label is query[:50] + '...' (config.py:247), so any two queries sharing a
50-character prefix produce byte-identical dropdown entries. Wide SELECT lists -
the app's whole purpose - collide almost always, since the distinguishing
WHERE/FROM clause sits past character 50.

Being precise about the mechanism matters, because it changes the fix: this is
NOT a lookup bug. _load_from_history uses history_combo.current(), and Tk stores
the selected index, so the right query is retrieved. The defect is that the USER
cannot tell the entries apart and has no way to pick the right one except by
trial.

Also checked: get_history_labels re-truncates to [:60] while the stored label is
already capped at 53, so the second bound is dead.

What the fix has to survive
---------------------------
"The two labels differ" is too weak a bar - it passes for a fix that merely
moves the collision somewhere else. Three harder cases are checked too:

  - queries differing only in the ELIDED MIDDLE. Middle-elision cannot separate
    these on its own, so something else must, or the finding is only narrowed.
  - a LEGACY history.json written by the old code, whose stored labels are
    already-collided prefixes. Users have one of these; a fix that only applies
    to newly-added queries leaves the defect exactly where it lives.
  - the length bound, across every label produced.
================================================================================
"""

REQUIRES_DISPLAY = True

import harness as h

PREFIX = "SELECT a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t FROM sales"
QUERY_A = f"{PREFIX} WHERE region = 'north'"
QUERY_B = f"{PREFIX} WHERE region = 'south'"

# Identical at both ends, differing only in the middle - the case elision alone
# cannot resolve.
MIDDLE_A = f"{PREFIX} JOIN staff ON s.id = 111111 ORDER BY total DESC LIMIT 10"
MIDDLE_B = f"{PREFIX} JOIN staff ON s.id = 999999 ORDER BY total DESC LIMIT 10"


def main():
    h.require_display()
    h.banner("F-27", "History labels collide; the dropdown becomes ambiguous")

    h.add_src_to_path()

    with h.isolated_home():
        import importlib
        import config as config_module
        importlib.reload(config_module)

        h.step("EXECUTED: two queries differing only past character 50")
        cfg = config_module.ConfigManager()
        cfg.add_to_history(QUERY_A)
        cfg.add_to_history(QUERY_B)

        h.detail("query A", QUERY_A)
        h.detail("query B", QUERY_B)
        h.detail("common prefix length",
                 len(PREFIX) if QUERY_A[:50] == QUERY_B[:50] else "< 50")

        labels = cfg.get_history_labels()
        for i, label in enumerate(labels):
            h.detail(f"label[{i}]", repr(label))
        identical = len(labels) == 2 and labels[0] == labels[1]
        h.detail("labels are byte-identical", identical)

        h.step("The dead second bound")
        stored = [entry.get("label", "") for entry in cfg.history]
        h.detail("stored label length (capped at 50 + 3)",
                 [len(s) for s in stored])
        h.detail("get_history_labels re-truncates to", "[:60]")
        h.detail("=> the [:60] bound can never bite", max(len(s) for s in stored) <= 60)

        h.step("Harder case: queries differing only in the elided middle")
        middle_cfg = config_module.ConfigManager()
        middle_cfg.history = []
        middle_cfg.add_to_history(MIDDLE_A)
        middle_cfg.add_to_history(MIDDLE_B)
        middle_labels = middle_cfg.get_history_labels()
        for i, label in enumerate(middle_labels):
            h.detail(f"label[{i}]", repr(label))
        middle_distinct = (len(middle_labels) == 2
                           and middle_labels[0] != middle_labels[1])
        h.detail("distinguishable", middle_distinct)
        if not middle_distinct:
            h.note("Elision alone cannot separate these - the ends match. "
                   "Something else has to, or the finding is only narrowed.")

        h.step("Harder case: a legacy history.json written by the old code")
        legacy_cfg = config_module.ConfigManager()
        # Exactly what the old add_to_history wrote: a collided prefix label,
        # stored on disk. This is what an existing user's file contains.
        legacy_cfg.history = [
            {"query": QUERY_A, "timestamp": "2026-08-18T09:00:00",
             "label": QUERY_A[:50] + "..."},
            {"query": QUERY_B, "timestamp": "2026-08-18T09:05:00",
             "label": QUERY_B[:50] + "..."},
        ]
        h.detail("stored labels (old style)",
                 [e["label"] for e in legacy_cfg.history])
        h.detail("stored labels collide",
                 legacy_cfg.history[0]["label"] == legacy_cfg.history[1]["label"])
        legacy_labels = legacy_cfg.get_history_labels()
        for i, label in enumerate(legacy_labels):
            h.detail(f"rebuilt label[{i}]", repr(label))
        legacy_fixed = (len(legacy_labels) == 2
                        and legacy_labels[0] != legacy_labels[1])
        h.detail("existing history is corrected on read", legacy_fixed)

        h.step("The length bound")
        try:
            from constants import HISTORY_LABEL_LENGTH
        except ImportError:
            # Pre-fix there is no such constant - the old code hard-coded the
            # bound at the call site. This script has to run against that build
            # too, or it cannot show the finding it exists to show.
            HISTORY_LABEL_LENGTH = 60
        every_label = labels + middle_labels + legacy_labels
        longest = max((len(lbl) for lbl in every_label), default=0)
        h.detail("configured bound", HISTORY_LABEL_LENGTH)
        h.detail("longest label", longest)
        within_bound = longest <= HISTORY_LABEL_LENGTH
        h.detail("within bound", within_bound)

        h.step("SOURCE half, now executed: does Combobox.current() still work?")
        with h.temp_dir() as tmp:
            with h.tk_app(output_dir=tmp) as app:
                app.config = cfg
                app._update_history_dropdown()
                values = list(app.history_combo["values"])
                h.detail("combobox values", [repr(v) for v in values])
                h.detail("values are duplicates",
                         len(values) == 2 and values[0] == values[1])

                results = []
                for idx in (0, 1):
                    app.history_combo.current(idx)
                    reported = app.history_combo.current()
                    retrieved = cfg.get_query_from_history(reported)
                    correct = retrieved == (QUERY_A if idx == 1 else QUERY_B)
                    # add_to_history inserts at position 0, so B is index 0.
                    results.append((idx, reported, correct))
                    h.detail(f"selected index {idx}",
                             f"current() -> {reported}, retrieves "
                             f"{'the right query' if correct else 'the WRONG query'}")

                lookup_ok = all(r[1] == r[0] and r[2] for r in results)
                h.detail("=> Tk returns the correct index despite duplicates",
                         lookup_ok)

                # Distinct labels are worth nothing if the widget cannot show
                # the part that differs. At width=40 a 60-character label was
                # clipped at exactly the point that distinguishes two entries,
                # so the dropdown still showed two identical strings after the
                # labels themselves had been fixed. What the user can actually
                # read is the test, not what the string contains.
                h.step("Can the widget actually show what distinguishes them?")
                visible_width = int(app.history_combo.cget("width"))
                h.detail("combobox width (characters)", visible_width)

                def visible(values):
                    return [v[:visible_width] for v in values]

                middle_visible = visible(middle_labels)
                legacy_visible = visible(legacy_labels)
                audit_visible = visible(list(app.history_combo["values"]))
                for name, shown in [("audit pair", audit_visible),
                                    ("elided middle", middle_visible),
                                    ("legacy", legacy_visible)]:
                    h.detail(f"{name} as displayed",
                             "DISTINCT" if len(set(shown)) == len(shown)
                             else f"IDENTICAL ON SCREEN: {shown[0]!r}")

                visibly_distinct = all(
                    len(set(shown)) == len(shown)
                    for shown in (audit_visible, middle_visible, legacy_visible)
                )
                h.detail("=> distinguishable on screen, not just in memory",
                         visibly_distinct)

                if identical:
                    h.step("So what is actually broken?")
                    h.note("Not the lookup. The user is shown two identical "
                           "strings and has no way to tell which is which "
                           "except by trial.")

    h.step("Contract check")
    h.detail("the audit's two queries produce identical labels", identical)
    h.detail("elided-middle queries distinguishable", middle_distinct)
    h.detail("legacy history corrected on read", legacy_fixed)
    h.detail("labels within the configured bound", within_bound)
    h.detail("distinguishable as displayed, not just as strings",
             visibly_distinct)
    h.detail("Combobox.current() returns the right index", lookup_ok)

    if (not identical and middle_distinct and legacy_fixed and within_bound
            and visibly_distinct):
        h.verdict("F-27", h.NOT_REPRODUCIBLE,
                  f"dropdown entries are distinguishable in all three cases: "
                  f"the audit's pair, which elision separates by keeping the "
                  f"trailing WHERE visible; queries differing only in the elided "
                  f"middle, which fall back to a timestamp; and a legacy "
                  f"history.json full of old-style collided prefixes, which is "
                  f"corrected on read rather than needing a migration. Labels "
                  f"stay within {HISTORY_LABEL_LENGTH} characters, the combobox "
                  f"is that wide so the distinguishing part is actually on "
                  f"screen rather than clipped, and the index lookup still "
                  f"returns the right query")
    elif identical and lookup_ok:
        h.verdict("F-27", h.CONFIRMED,
                  "two queries sharing a 50-char prefix produce byte-identical "
                  "dropdown labels; the SOURCE half is now executed - "
                  "ttk.Combobox.current() does return the correct index despite the "
                  "duplicates, so this is a display-ambiguity defect, not a lookup "
                  "bug, and get_history_labels' [:60] bound is dead against a stored "
                  "label already capped at 53")
    elif identical:
        h.verdict("F-27", h.CONFIRMED,
                  "labels collide, AND the index lookup no longer returns the right "
                  "query - this is worse than AUDIT.md recorded")
    else:
        h.verdict("F-27", h.NOT_REPRODUCIBLE,
                  f"labels are distinguishable: {labels}")


if __name__ == "__main__":
    main()
