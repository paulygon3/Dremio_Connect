"""
================================================================================
F-32 - Every requirement floor is unbounded                          (Medium)
================================================================================
requirements.txt declared five dependencies as unbounded `>=` floors, with no
upper bound, no lockfile, no pyproject.toml and no CI. The versions this
project was audited and repaired against were therefore whatever `pip install`
resolved to on the day, and nothing recorded which those were.

That matters more here than it usually would, because this repo's evidence is
its test suite. Every other script in this directory reports a verdict about
runtime behaviour - openpyxl's row ceiling, pyarrow's FlightStreamReader
surface, pandas' dtype handling. A verdict is only meaningful alongside the
versions it was produced against, and an unbounded floor means those versions
can change underneath the whole suite without a single line of the repo
changing.

The drift was already real rather than theoretical: the file said
pandas>=1.5.0 and pyarrow>=10.0.0, while a fresh install resolved to pandas
3.0.5 and pyarrow 25.0.1 - several major versions on, across documented
breaking changes. The audit hit one directly, an `OptionError: No such
keys(s): 'io.excel.zip.reader'` from a pandas 3.0 internal path that does not
exist at the declared floor.

What this script checks
-----------------------
Two things, and the second is the one with ongoing value:

  1. every requirement is pinned exactly, so the file states a version rather
     than a wish
  2. the pinned version is the version actually installed here

(2) makes this a drift detector for the rest of the suite. If someone upgrades
a dependency without re-running the repros, this script says so, and every
other verdict in the summary should be treated as stale until it is re-run.
================================================================================
"""

REQUIRES_DISPLAY = False

import importlib.metadata as metadata
import re

import harness as h

REQUIREMENTS = h.SRC_DIR / "requirements.txt"

# name, operator, version - the operator is captured so an unpinned floor is
# reported as what it is rather than merely failing to match.
REQUIREMENT_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==|>=|<=|~=|>|<|!=)?\s*([^\s;#]+)?"
)


def parse_requirements():
    """Every non-comment, non-blank line, as (name, operator, version)."""
    parsed = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = REQUIREMENT_RE.match(line)
        if not match:
            parsed.append((line, None, None))
            continue
        name, operator, version = match.groups()
        parsed.append((name, operator, version))
    return parsed


def installed_version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def main():
    h.banner("F-32", "Are the dependencies pinned, and is that what is installed?")

    h.step("What requirements.txt declares, and what is installed")
    requirements = parse_requirements()

    rows = []
    unpinned = []
    mismatched = []
    missing = []

    for name, operator, version in requirements:
        actual = installed_version(name)

        if operator != "==" or not version:
            unpinned.append(name)
            state = f"UNPINNED ({operator or 'no specifier'})"
        elif actual is None:
            missing.append(name)
            state = "NOT INSTALLED"
        elif actual != version:
            mismatched.append((name, version, actual))
            state = "DRIFTED"
        else:
            state = "ok"

        rows.append([name, f"{operator or ''}{version or ''}",
                     actual or "-", state])

    h.table(["requirement", "declared", "installed", ""], rows)

    # The optional dependencies are guarded imports, so a missing one is a
    # degraded feature rather than a broken app. It still has to be reported -
    # a verdict produced without Pillow is not a verdict about the same program
    # as one produced with it.
    h.step("Contract check")
    h.detail("requirements parsed", len(requirements))
    h.detail("unpinned", unpinned or "none")
    h.detail("declared but not installed", missing or "none")
    h.detail("installed version differs from the pin",
             [f"{n}: pinned {p}, installed {a}" for n, p, a in mismatched]
             or "none")

    if unpinned:
        h.verdict("F-32", h.CONFIRMED,
                  f"{len(unpinned)} of {len(requirements)} requirements are not "
                  f"pinned exactly ({', '.join(unpinned)}), so the versions this "
                  f"suite's verdicts were produced against are not recorded "
                  f"anywhere and a later install can differ silently")
    elif mismatched:
        h.verdict("F-32", h.CONFIRMED,
                  f"pinned, but the environment has drifted from the pins: "
                  f"{'; '.join(f'{n} pinned {p} but {a} installed' for n, p, a in mismatched)}"
                  f". Every other verdict in this suite was produced against "
                  f"whatever is installed, not against what the file declares, "
                  f"so they should be re-run")
    elif missing:
        h.verdict("F-32", h.NOT_REPRODUCIBLE,
                  f"all {len(requirements)} requirements are pinned exactly, and "
                  f"every installed one matches its pin. Not installed here: "
                  f"{', '.join(missing)} - both are guarded imports, so this is a "
                  f"degraded feature rather than a failure, but any verdict about "
                  f"them was produced without them")
    else:
        h.verdict("F-32", h.NOT_REPRODUCIBLE,
                  f"all {len(requirements)} requirements are pinned exactly and "
                  f"every one matches the version installed here, so the rest of "
                  f"this suite's verdicts have a recorded runtime behind them "
                  f"rather than whatever pip resolved on the day")


if __name__ == "__main__":
    main()
