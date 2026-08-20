"""
================================================================================
harness.py - Shared plumbing for the Dremio_To_Excel repro suite
================================================================================
Every repro script imports this. It provides:

    - sys.path setup so the app's absolute imports (`from constants import ...`)
      resolve, matching how main.py is actually run (INVENTORY.md §5.1)
    - an isolated $HOME so no test ever touches the developer's real
      ~/.dremioexporter/ directory
    - a real DremioExporter built on a withdrawn Tk root, so tests drive the
      *actual* source rather than a transcription of it
    - verdict reporting in the format run_all.py parses

Design note - why tests drive real source
-----------------------------------------
AUDIT.md's method for the Excel-limit findings was to copy the body of
`_export_to_excel` verbatim and run it against synthetic frames. That proves the
bug but makes a useless regression test: a copy keeps reporting CONFIRMED after
the original is fixed. These scripts instead build a real DremioExporter under
Xvfb and call the real method, so a Stage 1 fix flips the verdict to
NOT REPRODUCIBLE on its own. That is only possible because a display is now
available; it was not when the audit was written.
================================================================================
"""

import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent
REPO_ROOT = SRC_DIR.parent

CONFIRMED = "CONFIRMED"
NOT_REPRODUCIBLE = "NOT REPRODUCIBLE"
BLOCKED = "STILL BLOCKED"

_VALID_STATUSES = (CONFIRMED, NOT_REPRODUCIBLE, BLOCKED)


# =============================================================================
# IMPORT SETUP
# =============================================================================

def add_src_to_path():
    """
    Put dremio_Tool/ on sys.path.

    The app's submodules use absolute imports (`from constants import ...`)
    while __init__.py uses relative ones, so the package cannot be imported as a
    package (INVENTORY.md §5.1). Importing the modules directly with
    dremio_Tool/ on sys.path is the only working arrangement, and is what
    `python main.py` produces.
    """
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))


# =============================================================================
# REPORTING
# =============================================================================

def banner(finding, title):
    """Print the header for a repro script."""
    line = "=" * 78
    print(line)
    print(f"{finding} - {title}")
    print(line)


def step(msg):
    """Print a numbered-ish narrative step."""
    print(f"\n--- {msg}")


def detail(key, value):
    """Print an indented key/value observation."""
    print(f"      {key}: {value}")


def note(msg):
    """Print an indented free-text note."""
    print(f"      {msg}")


def table(headers, rows):
    """Print a simple aligned table."""
    cols = [len(h) for h in headers]
    rows = [[str(c) for c in r] for r in rows]
    for r in rows:
        for i, c in enumerate(r):
            cols[i] = max(cols[i], len(c))
    fmt = "      " + "  ".join(f"{{:<{w}}}" for w in cols)
    print(fmt.format(*headers))
    print("      " + "  ".join("-" * w for w in cols))
    for r in rows:
        print(fmt.format(*r))


def verdict(finding, status, note_text):
    """
    Emit a machine-readable verdict line.

    run_all.py greps for the VERDICT| prefix, so the format is load-bearing.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"bad status {status!r}; expected one of {_VALID_STATUSES}")
    print(f"\nVERDICT|{finding}|{status}|{note_text}")


# =============================================================================
# ENVIRONMENT ISOLATION
# =============================================================================

@contextlib.contextmanager
def isolated_home():
    """
    Redirect $HOME (and %APPDATA%) to a throwaway directory.

    ConfigManager._setup_directories resolves its app dir from Path.home() on
    POSIX and $APPDATA on Windows, and creates it on construction. Without this
    every config/credential test would read and write the developer's real
    ~/.dremioexporter/.

    Must wrap ConfigManager()/DremioExporter() construction, not just the
    assertions - the path is captured in __init__.
    """
    tmp = Path(tempfile.mkdtemp(prefix="dremio_repro_home_"))
    saved = {k: os.environ.get(k) for k in ("HOME", "APPDATA", "USERPROFILE")}
    try:
        os.environ["HOME"] = str(tmp)
        os.environ["APPDATA"] = str(tmp)
        os.environ["USERPROFILE"] = str(tmp)
        yield tmp
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def temp_dir(prefix="dremio_repro_"):
    """A throwaway working directory."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def app_data_dir():
    """The app-data directory ConfigManager will use for the current $HOME."""
    add_src_to_path()
    from constants import APP_NAME
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", "")) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


# =============================================================================
# TK APPLICATION HARNESS
# =============================================================================

def require_display():
    """
    Abort with a clear message if there is no display.

    Scripts that set REQUIRES_DISPLAY = True call this first so that running
    them bare (without xvfb-run) fails with an instruction rather than a
    TclError traceback.
    """
    if not os.environ.get("DISPLAY"):
        print("STILL BLOCKED: no $DISPLAY.")
        print("Run this script as:  xvfb-run -a python <script>")
        print("or run the whole suite with:  python dremio_Tool/tests/run_all.py")
        sys.exit(2)


def set_entry(entry, value):
    """Replace the contents of a ttk.Entry."""
    entry.delete(0, "end")
    entry.insert(0, str(value))


class StubConnection:
    """
    A stand-in for DremioConnection that needs no server.

    Scripts whose finding is about the app's own logic - truncation reporting,
    filename handling, error dialogs - do not need a Flight server, only
    something shaped like a connection. Use flightserver.py instead whenever
    the finding is about the transport itself.

    It lives here rather than in each script because it has to track
    DremioConnection's signature. When execute_query gained cancel_event
    (F-13), three private copies of this class did not, and every one of them
    turned into a TypeError inside the worker - which the scripts dutifully
    reported as a failed export. One copy is one place to keep in step.

    Args:
        df: DataFrame to return from execute_query.
        raise_exc: exception instance to raise instead of returning.
    """

    is_connected = True

    def __init__(self, df=None, raise_exc=None):
        self.df = df
        self.raise_exc = raise_exc
        self.cancel_calls = 0

    def execute_query(self, query, on_status=None, cancel_event=None):
        if on_status:
            on_status("Retrieving data...")
        if self.raise_exc:
            raise self.raise_exc
        return self.df

    def cancel_query(self):
        self.cancel_calls += 1
        return False

    def disconnect(self):
        self.is_connected = False


@contextlib.contextmanager
def quiet_stdout():
    """Swallow stdout. Used to keep utils.py's asset-loader prints out of reports."""
    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = saved


@contextlib.contextmanager
def tk_app(output_dir=None, filename="repro_export.xlsx",
           autofit=True, freeze_header=True, open_after=False,
           remember_token=True, quiet=True):
    """
    Build a real DremioExporter on a withdrawn Tk root.

    Must be used inside isolated_home() - DremioExporter.__init__ constructs a
    ConfigManager as its first statement (app.py:54), which creates the app-data
    directory immediately.

    Widget values are set after construction because _load_saved_settings
    populates them from config during __init__.

    The window is withdrawn rather than shown: every finding here is about
    logic, threading, or file output, none of which needs a mapped window, and
    withdrawing keeps the suite fast.
    """
    add_src_to_path()
    import tkinter as tk
    from app import DremioExporter

    root = tk.Tk()
    root.withdraw()
    # utils.load_icon / load_logo_image print INFO and ERROR lines on every
    # construction (the .ico is Windows-only, so it always fails here). That is
    # F-20 territory, not the finding under test, so keep it out of the report.
    if quiet:
        with quiet_stdout():
            app = DremioExporter(root)
    else:
        app = DremioExporter(root)

    if output_dir is not None:
        set_entry(app.output_dir, output_dir)
    set_entry(app.filename, filename)
    app.autofit.set(autofit)
    app.freeze_header.set(freeze_header)
    app.open_after.set(open_after)
    app.remember_token.set(remember_token)

    try:
        yield app
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def run_with_mainloop(root, fn, start_delay_ms=50):
    """
    Run `fn` on the Tk main thread with mainloop() genuinely running.

    This is REQUIRED for any test involving the app's worker threads, and the
    reason is not cosmetic. Tkinter permits a non-Tk thread to call
    root.after(...) only while the main thread is inside mainloop(): _tkinter
    sets a `dispatching` flag there, and without it every cross-thread after()
    raises "RuntimeError: main thread is not in main loop" before the code under
    test can misbehave.

    Servicing the queue with root.update() instead does NOT set that flag. A
    harness built on update() alone makes _execute_thread die at its first
    progress callback, which looks like a finding and is really a test artifact.

    fn runs inside an after() callback, so it may block; call pump(root, ...)
    from within it when queued callbacks need to be dispatched.
    """
    box = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:      # noqa: BLE001 - re-raised below
            box["error"] = exc
        finally:
            # The scenario may legitimately have destroyed the root (the
            # close-during-query tests do); quit() on a dead interpreter raises.
            try:
                root.quit()
            except Exception:
                pass

    root.after(start_delay_ms, runner)
    root.mainloop()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def pump(root, seconds):
    """
    Run the Tk event loop for a fixed period without calling mainloop().

    The app marshals every UI update back with root.after(0, ...), so those
    callbacks only run when something services the event queue. Tests drive it
    explicitly instead of starting a mainloop they would then have to stop.
    """
    import time
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            root.update()
        except Exception:
            return
        time.sleep(0.01)


def wait_for(root, predicate, timeout=30.0):
    """
    Pump the event loop until `predicate()` is true or the timeout expires.

    Returns True if the predicate became true.
    """
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        try:
            root.update()
        except Exception:
            return predicate()
        time.sleep(0.01)
    return predicate()


def new_threads_from(before):
    """Threads that appeared since the `before` snapshot (a set of Thread objects)."""
    import threading
    return [t for t in threading.enumerate() if t not in before]


@contextlib.contextmanager
def captured_dialogs(answer_yes=True):
    """
    Replace tkinter.messagebox functions with recorders.

    Several findings turn on *which* dialog the user is shown - F-16's whole
    point is that the success dialog is never reached. A real messagebox would
    block the suite forever, so record the calls instead and assert on them.

    askyesno is stubbed too, since the overwrite prompt (F-25) is a question
    rather than a notification. `answer_yes` chooses the answer, so a test can
    drive both the "overwrite" and the "cancel" branch.

    Yields a dict with keys 'info', 'error', 'warning' and 'askyesno'.
    """
    add_src_to_path()
    import app as app_module

    calls = {"info": [], "error": [], "warning": [], "askyesno": []}
    mb = app_module.messagebox
    saved = (mb.showinfo, mb.showerror, mb.showwarning, mb.askyesno)

    def _ask(title="", message="", **kw):
        calls["askyesno"].append((title, message))
        return answer_yes

    mb.showinfo = lambda title="", message="", **kw: calls["info"].append((title, message))
    mb.showerror = lambda title="", message="", **kw: calls["error"].append((title, message))
    mb.showwarning = lambda title="", message="", **kw: calls["warning"].append((title, message))
    mb.askyesno = _ask
    try:
        yield calls
    finally:
        mb.showinfo, mb.showerror, mb.showwarning, mb.askyesno = saved


@contextlib.contextmanager
def chosen_file(path):
    """
    Make the file dialogs return `path` without opening anything.

    The Load / Save / Save-log callbacks (F-18) begin with a filedialog call, so
    testing what they do with the file they were given means answering that
    dialog. Stubbing it is also what lets a repro drive the REAL callback rather
    than the helper underneath it - which matters, because a helper that only
    exists after the fix cannot show what the unfixed build did.

    Pass '' to simulate the user cancelling.
    """
    add_src_to_path()
    import app as app_module

    fd = app_module.filedialog
    saved = (fd.asksaveasfilename, fd.askopenfilename)
    fd.asksaveasfilename = lambda **kw: str(path)
    fd.askopenfilename = lambda **kw: str(path)
    try:
        yield
    finally:
        fd.asksaveasfilename, fd.askopenfilename = saved


@contextlib.contextmanager
def named_save(name):
    """
    Answer the "Name for this query" prompt with `name`.

    The saved-query library asks for a name rather than a path (F-31), so
    driving Save means answering simpledialog.askstring. Pass None to simulate
    the user cancelling.
    """
    add_src_to_path()
    import app as app_module

    # Pre-fix, app.py does not import simpledialog at all - Save asked for a
    # PATH through filedialog. Leaving that unpatched would open a real dialog
    # under Xvfb and block until the suite timeout, so it is answered as a
    # cancellation: nothing is written, which is the honest pre-fix result for a
    # test about the library, and the run finishes instead of hanging.
    sd = getattr(app_module, "simpledialog", None)
    if sd is None:
        fd = app_module.filedialog
        saved_fd = (fd.asksaveasfilename, fd.askopenfilename)
        fd.asksaveasfilename = lambda **kw: ""
        fd.askopenfilename = lambda **kw: ""
        try:
            yield
        finally:
            fd.asksaveasfilename, fd.askopenfilename = saved_fd
        return

    saved = sd.askstring
    sd.askstring = lambda *a, **kw: name
    try:
        yield
    finally:
        sd.askstring = saved


@contextlib.contextmanager
def captured_stderr():
    """
    Capture Python-level stderr writes into a StringIO.

    Used by F-17, where Tk prints a callback traceback to stderr and the audit's
    claim is that a windowed PyInstaller build has no stderr to print to.

    Note this does not capture Arrow's C++ logging, which writes to fd 2
    directly - see silence_fd_stderr for that.
    """
    buf = io.StringIO()
    saved = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = saved


@contextlib.contextmanager
def silence_fd_stderr():
    """
    Silence writes to file descriptor 2, including from C++ extensions.

    pyarrow's Flight layer logs client-middleware errors from C++ straight to
    fd 2. The app's own DremioClientAuthMiddleware raises on any call where the
    server did not attach an authorization header, which is normal and
    non-fatal, but it buries the readable output of the Flight-backed tests.
    """
    saved_fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        sys.stderr.flush()
        os.dup2(devnull, 2)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved_fd, 2)
        os.close(devnull)
        os.close(saved_fd)


# =============================================================================
# SOURCE INSPECTION
# =============================================================================

def source_text(filename):
    """Read one of the app's source files."""
    return (SRC_DIR / filename).read_text(encoding="utf-8")


def grep_source(pattern, filenames=None):
    """
    Search the app's real source for a regex.

    Returns a list of (filename, lineno, line). Used for the STATIC half of
    several findings - 'this code exists / does not exist' claims stay true
    against the real tree rather than against a snapshot.
    """
    if filenames is None:
        filenames = ["main.py", "app.py", "config.py", "connection.py",
                     "utils.py", "constants.py", "__init__.py"]
    hits = []
    rx = re.compile(pattern)
    for name in filenames:
        path = SRC_DIR / name
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if rx.search(line):
                hits.append((name, i, line.strip()))
    return hits


# =============================================================================
# MEASUREMENT
# =============================================================================

def peak_rss_mb():
    """
    Process peak RSS in MB (ru_maxrss).

    This is a monotonic high-water mark: it never decreases, so a delta between
    two samples is 'how much the peak rose', not 'how much was resident'. This
    is the metric AUDIT.md F-08 used, and the caveat it carries.
    """
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def current_rss_mb():
    """
    Process *current* RSS in MB, read from /proc/self/statm.

    Unlike ru_maxrss this falls when memory is released, so sampling it at each
    stage measures concurrent residency - exactly the thing F-08's table could
    not distinguish. Linux only; returns None elsewhere.
    """
    try:
        with open("/proc/self/statm", "r") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0)
    except (OSError, IndexError, ValueError):
        return None
