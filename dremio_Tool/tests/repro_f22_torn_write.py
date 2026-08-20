"""
================================================================================
F-22 - Non-atomic writes; a torn write silently resets all settings   (Medium)
================================================================================
_save_json (config.py:165-177) opens with mode 'w', which truncates
immediately, then writes. There is no temp-file-plus-os.replace, no fsync, and
no backup copy, so there is a window in which the file on disk is empty or
partial.

The window is not theoretical. _save_json is called from _on_close
(app.py:506) immediately before root.destroy(), and from add_to_history on
every Execute. A machine shutdown, force-quit, or full disk during that window
truncates the file.

The result lands in the *survivable* column of F-21, which is the problem:
_load_json swallows it, returns defaults, and the app opens looking
factory-fresh. Hostname, username, output folder, filename pattern, window size
and last query are all gone, and the only notice is a print() to a console that
does not exist in a windowed build (F-20).

Reproduced by interrupting the write at two points - after truncation but
before any content, and part-way through - then reopening with a real
ConfigManager and listing what was lost.
================================================================================
"""

REQUIRES_DISPLAY = False

import ast
import json

import harness as h

SETTINGS = {
    ("connection", "hostname"): "dremio.example.com",
    ("connection", "username"): "alice",
    ("connection", "port"): "32010",
    ("output", "directory"): "/home/alice/Documents/Dremio_Exports",
    ("output", "filename_pattern"): "quarterly_{timestamp}.xlsx",
    ("ui", "window_width"): 1440,
    ("ui", "last_query"): "SELECT * FROM finance.ledger LIMIT 100",
}


def calls_in(func_name, filename, wanted):
    """
    Which of `wanted` calls appear inside a given function.

    By AST rather than grep, because the fix explains itself in a comment that
    names os.replace and fsync; a grep counted that prose as the machinery it
    was looking for, which would report the fix present before it was written.
    """
    tree = ast.parse(h.source_text(filename))
    found = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or func.name != func_name:
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                dotted = node.func.attr
                if isinstance(node.func.value, ast.Name):
                    dotted = f"{node.func.value.id}.{dotted}"
                if dotted in wanted:
                    found.append((dotted, node.lineno))
    return found


def check_write_mechanics():
    h.step("STATIC: how does _save_json write?")
    machinery = calls_in("_save_json", "config.py",
                         {"os.replace", "os.rename", "os.fsync", "f.flush"})
    for dotted, lineno in machinery:
        h.detail(f"config.py:{lineno}", dotted)
    names = {d for d, _ in machinery}
    atomic = bool(names & {"os.replace", "os.rename"})
    durable = "os.fsync" in names
    h.detail("replaces rather than truncates in place", atomic)
    h.detail("flushes to disk before the replace", durable)

    h.step("STATIC: when is it called?")
    for name, lineno, line in h.grep_source(
            r"_save_current_settings\(\)|self\.root\.destroy\(\)|save_history\(\)",
            ["app.py", "config.py"]):
        h.detail(f"{name}:{lineno}", line)
    return atomic and durable


def populate(cfg):
    for (section, key), value in SETTINGS.items():
        cfg.set(section, key, value)
    cfg.save_config()


def surviving_settings(cfg):
    kept, lost = [], []
    for (section, key), value in SETTINGS.items():
        if cfg.get(section, key, None) == value:
            kept.append(f"{section}.{key}")
        else:
            lost.append(f"{section}.{key}")
    return kept, lost


def tear_write(fraction, label):
    """
    Interrupt _save_json part-way and report what a reopen recovers.

    json.dump is replaced with one that writes `fraction` of the payload and
    then raises, standing in for a power loss or force-quit. The truncation has
    already happened by then - that is the whole point, and it is done by
    open(..., 'w') before json.dump is ever called.
    """
    h.add_src_to_path()
    import importlib
    import config as config_module
    importlib.reload(config_module)

    with h.isolated_home():
        cfg = config_module.ConfigManager()
        populate(cfg)

        saved_path = cfg.config_file
        good_size = saved_path.stat().st_size
        kept, lost = surviving_settings(config_module.ConfigManager())
        h.detail(f"{label}: settings persisted before the tear",
                 f"{len(kept)}/{len(SETTINGS)} ({good_size} bytes on disk)")

        real_dump = json.dump

        def torn_dump(data, fp, **kwargs):
            payload = json.dumps(data, **{k: v for k, v in kwargs.items()
                                          if k in ("indent", "ensure_ascii")})
            cut = int(len(payload) * fraction)
            fp.write(payload[:cut])
            raise OSError("simulated interruption (power loss / force-quit / disk full)")

        config_module.json.dump = torn_dump
        try:
            cfg.set("connection", "hostname", "dremio.example.com")
            cfg.save_config()   # -> _save_json -> open('w') truncates, then tears
        finally:
            config_module.json.dump = real_dump

        torn_size = saved_path.stat().st_size
        h.detail(f"{label}: file size after the interrupted write",
                 f"{torn_size} bytes (was {good_size})")

        raised = None
        try:
            reopened = config_module.ConfigManager()
        except Exception as e:
            raised = f"{type(e).__name__}: {e}"
            h.detail(f"{label}: reopening raised", raised)
            return {"label": label, "raised": raised, "lost": list(SETTINGS)}

        kept, lost = surviving_settings(reopened)
        h.detail(f"{label}: settings recovered on reopen",
                 f"{len(kept)}/{len(SETTINGS)}")
        h.detail(f"{label}: lost", lost if lost else "nothing")
        return {"label": label, "raised": None, "lost": lost}


def check_failure_is_reported():
    """
    F-20's half: when a save fails, does anyone hear about it?

    print() was the only error channel in the persistence layer, and this app
    ships as a windowed PyInstaller build with no console attached. A save
    could fail on every Execute and the user would never know.
    """
    h.step("A save that fails outright - who is told?")
    h.add_src_to_path()
    import importlib
    import config as config_module
    importlib.reload(config_module)

    heard = []
    with h.isolated_home():
        cfg = config_module.ConfigManager()
        cfg.on_warning = heard.append

        real_replace = config_module.os.replace
        config_module.os.replace = lambda *a, **kw: (_ for _ in ()).throw(
            OSError("No space left on device"))
        try:
            cfg.set("connection", "hostname", "dremio.example.com")
            ok = cfg.save_config()
        finally:
            config_module.os.replace = real_replace

    h.detail("save_config reported failure to its caller", ok is False)
    h.detail("warnings raised", heard or "NONE")
    printed_only = not heard
    if printed_only:
        h.note("Nothing reached the callback, so the only trace is stdout - "
               "which a windowed build does not have.")
    return bool(heard) and ok is False


def main():
    h.banner("F-22", "Non-atomic writes silently reset all settings")

    atomic = check_write_mechanics()

    h.step("Tearing the write at two points")
    h.note("The interruption is injected inside json.dump - after the file has "
           "been opened for writing, which is the window that mattered.")
    results = [
        tear_write(0.0, "torn at 0% (nothing written)"),
        tear_write(0.4, "torn at 40% (partial JSON)"),
    ]

    reported = check_failure_is_reported()

    h.step("Contract check")
    total_lost = max(len(r["lost"]) for r in results)
    h.detail("settings lost to a torn write",
             f"{total_lost}/{len(SETTINGS)}")
    h.detail("write is atomic and durable", atomic)
    h.detail("a failed save is reported to the user, not to a console",
             reported)

    if total_lost == 0 and atomic and reported:
        h.verdict("F-22", h.NOT_REPRODUCIBLE,
                  f"_save_json writes to a temp file, fsyncs it and renames it "
                  f"over the target, so both tear points left all "
                  f"{len(SETTINGS)}/{len(SETTINGS)} settings intact - and the "
                  f"failure is now reported through ConfigManager.on_warning "
                  f"rather than a print() no windowed build can show (F-20)")
    elif total_lost:
        h.verdict("F-22", h.CONFIRMED,
                  f"an interruption mid-write loses up to {total_lost}/"
                  f"{len(SETTINGS)} settings (hostname, username, output folder, "
                  f"filename pattern, window size, last query)")
    else:
        h.verdict("F-22", h.CONFIRMED,
                  f"nothing was lost, but the mechanics are incomplete: "
                  f"atomic_and_durable={atomic} failure_reported={reported}")


if __name__ == "__main__":
    main()
