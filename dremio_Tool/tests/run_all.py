#!/usr/bin/env python3
"""
================================================================================
run_all.py - Run the whole repro suite with one command
================================================================================
    python dremio_Tool/tests/run_all.py

Each repro script runs in its own subprocess, so a crash or a Tk teardown in one
cannot affect the others. Scripts that declare REQUIRES_DISPLAY = True are
wrapped in `xvfb-run -a` automatically when $DISPLAY is unset, so no wrapper is
needed at the top level. If you are already under a display (or run the whole
suite under `xvfb-run -a python run_all.py`), they run directly and share it.

Options:
    -v, --verbose     print each script's full output as it runs
    --only F-13,F-14  run only the scripts covering these findings
    --list            list the scripts and their display requirement, run nothing
    --timeout N       per-script timeout in seconds (default 600)

Exit status:
    0  every script completed (whatever its verdicts)
    1  at least one script crashed or timed out
================================================================================
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

VERDICT_RE = re.compile(r"^VERDICT\|([^|]+)\|([^|]+)\|(.*)$", re.MULTILINE)
DISPLAY_RE = re.compile(r"^REQUIRES_DISPLAY\s*=\s*(True|False)", re.MULTILINE)
FINDING_RE = re.compile(r"repro_(f\d+)")

STATUS_ORDER = {"CONFIRMED": 0, "NOT REPRODUCIBLE": 1, "STILL BLOCKED": 2}


def discover():
    """Find repro scripts, in finding order, with their display requirement."""
    scripts = []
    for path in sorted(TESTS_DIR.glob("repro_*.py")):
        text = path.read_text(encoding="utf-8")
        match = DISPLAY_RE.search(text)
        needs_display = bool(match) and match.group(1) == "True"
        findings = FINDING_RE.findall(path.name)
        key = findings[0] if findings else path.stem
        scripts.append({"path": path, "needs_display": needs_display, "key": key})
    return scripts


def command_for(script):
    """Wrap in xvfb-run only when a display is needed and none is present."""
    base = [sys.executable, str(script["path"])]
    if script["needs_display"] and not os.environ.get("DISPLAY"):
        return ["xvfb-run", "-a"] + base
    return base


def run(script, timeout, verbose):
    started = time.time()
    try:
        proc = subprocess.run(
            command_for(script),
            cwd=str(TESTS_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = proc.stdout
        crashed = proc.returncode != 0
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        return {"script": script, "verdicts": [], "crashed": True,
                "elapsed": time.time() - started,
                "reason": f"timed out after {timeout}s", "output": ""}
    except FileNotFoundError as e:
        return {"script": script, "verdicts": [], "crashed": True,
                "elapsed": time.time() - started,
                "reason": f"could not launch: {e}", "output": ""}

    verdicts = [{"finding": f.strip(), "status": s.strip(), "note": n.strip()}
                for f, s, n in VERDICT_RE.findall(output)]

    reason = ""
    if crashed:
        tail = [ln for ln in stderr.strip().splitlines() if ln.strip()][-1:]
        reason = f"exit {proc.returncode}" + (f": {tail[0][:120]}" if tail else "")
    elif not verdicts:
        crashed = True
        reason = "produced no VERDICT line"

    if verbose:
        print(output)
        if stderr.strip() and crashed:
            print(stderr[-2000:], file=sys.stderr)

    return {"script": script, "verdicts": verdicts, "crashed": crashed,
            "elapsed": time.time() - started, "reason": reason, "output": output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--only", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    scripts = discover()

    if args.only:
        wanted = {w.strip().lower().replace("-", "")
                  for w in args.only.split(",") if w.strip()}
        scripts = [s for s in scripts if s["key"].lower() in wanted]
        if not scripts:
            print(f"No scripts match --only {args.only}")
            return 1

    if args.list:
        print(f"{len(scripts)} repro scripts in {TESTS_DIR}\n")
        for s in scripts:
            flag = "xvfb-run -a" if s["needs_display"] else "-"
            print(f"  {s['path'].name:<44} {flag}")
        print("\nScripts marked 'xvfb-run -a' need a display; run_all.py adds the")
        print("wrapper automatically when $DISPLAY is unset.")
        return 0

    have_display = bool(os.environ.get("DISPLAY"))
    gui = sum(1 for s in scripts if s["needs_display"])
    print(f"Running {len(scripts)} repro scripts ({gui} need a display).")
    print(f"$DISPLAY is {'set: ' + os.environ['DISPLAY'] if have_display else 'unset'}"
          f" - GUI scripts will {'run directly' if have_display else 'be wrapped in xvfb-run -a'}.")
    print()

    results = []
    for script in scripts:
        label = script["path"].name
        marker = "D" if script["needs_display"] else " "
        print(f"  [{marker}] {label:<44}", end="", flush=True)
        result = run(script, args.timeout, args.verbose)
        results.append(result)
        if result["crashed"]:
            print(f"  CRASHED ({result['reason']})  {result['elapsed']:.1f}s")
        else:
            statuses = ", ".join(f"{v['finding']} {v['status']}"
                                 for v in result["verdicts"])
            print(f"  {statuses}  {result['elapsed']:.1f}s")

    # ---- Summary --------------------------------------------------------
    all_verdicts = [v for r in results for v in r["verdicts"]]
    all_verdicts.sort(key=lambda v: (STATUS_ORDER.get(v["status"], 9), v["finding"]))

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)

    width = max((len(v["finding"]) for v in all_verdicts), default=6)
    for verdict in all_verdicts:
        note = verdict["note"]
        if len(note) > 300:
            note = note[:297] + "..."
        print(f"  {verdict['finding']:<{width}}  {verdict['status']:<16}  {note}")

    counts = {}
    for verdict in all_verdicts:
        counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1

    print("\n" + "-" * 78)
    total_time = sum(r["elapsed"] for r in results)
    print(f"  {len(all_verdicts)} findings across {len(results)} scripts "
          f"in {total_time:.0f}s")
    for status in ("CONFIRMED", "NOT REPRODUCIBLE", "STILL BLOCKED"):
        if status in counts:
            print(f"    {status:<16} {counts[status]}")

    crashed = [r for r in results if r["crashed"]]
    if crashed:
        print(f"\n  {len(crashed)} script(s) did not complete:")
        for r in crashed:
            print(f"    {r['script']['path'].name}: {r['reason']}")
        print("  Re-run one with -v to see its output.")

    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(main())
