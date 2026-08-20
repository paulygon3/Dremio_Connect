#!/usr/bin/env bash
#
# Bring a fresh container up to the state the repro suite was verified in.
#
# Two things are installed here rather than assumed, because neither is in the
# repository and the suite is meaningless without them:
#
#   xvfb         22 of the 29 repro scripts build a real Tk application. Without
#                a display they report STILL BLOCKED, which reads like a
#                regression in the app and is not one.
#   the pins     the 6 exact versions in dremio_Tool/requirements.txt. These are
#                what every verdict in the suite was measured against (F-32);
#                nothing else installs them.
#
# The three checks at the end verify rather than assume. They warn instead of
# failing, so the container still comes up and you can see what is wrong.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

warned=0
warn() {
    warned=1
    echo ""
    echo "  !! $*"
    echo ""
}

echo "==> Installing Xvfb (needed by 22 of the 29 repro scripts)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb

echo "==> Installing the pinned dependencies"
pip install --quiet --requirement dremio_Tool/requirements.txt

# ---------------------------------------------------------------------------
# Verification. Each of these has failed for real at some point in this repo's
# history, which is why they are checked at create time rather than discovered
# half way through a test run.
# ---------------------------------------------------------------------------

echo "==> Checking the interpreter version"
python_version="$(python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
case "$python_version" in
    3.12.*)
        echo "    Python $python_version - matches what the suite was verified against"
        ;;
    *)
        warn "Python is $python_version, not the 3.12.x that dremio_Tool/requirements.txt
     was verified against. The pins there are exact, so a different minor
     version may have no wheel for pandas==3.0.5 / numpy==2.5.2, and any
     verdict the suite produces is against an unrecorded runtime. Read the
     note at the top of requirements.txt before changing a pin."
        ;;
esac

echo "==> Checking that Tk runs headless"
if xvfb-run -a python -c "import tkinter; r = tkinter.Tk(); print('    Tk OK', r.winfo_screenwidth())"; then
    :
else
    warn "Tk did not start under xvfb-run. 22 of the 29 repro scripts will report
     STILL BLOCKED - that is this, not a regression in the application.
     See dremio_Tool/tests/README.md for the control experiment."
fi

echo "==> Checking the installed versions against the pins"
if python dremio_Tool/tests/repro_f32_unpinned_requirements.py 2>&1 \
        | grep -q '^VERDICT|F-32|NOT REPRODUCIBLE'; then
    echo "    all 6 pins match what is installed"
else
    warn "The installed packages do not match the pins in requirements.txt.
     repro_f32 doubles as the drift detector for every other verdict in the
     suite, so fix this before trusting a run. Details:
       python dremio_Tool/tests/repro_f32_unpinned_requirements.py"
fi

echo ""
echo "=============================================================================="
if [ "$warned" -eq 0 ]; then
    echo " Environment ready."
else
    echo " Environment set up WITH WARNINGS - see the !! lines above."
fi
cat <<'NEXT'

 One thing this script cannot do for you:

   Tell you where the work is up to. Read CLAUDE.md first - it names the
   documents that matter and the three things not to get wrong.

 To confirm the environment end to end (about 7 minutes):

   python dremio_Tool/tests/run_all.py

 A clean run is 32 NOT REPRODUCIBLE, 1 CONFIRMED. The CONFIRMED one is F-28's
 encoding half and is expected - it is a decision, not a regression.
==============================================================================
NEXT
