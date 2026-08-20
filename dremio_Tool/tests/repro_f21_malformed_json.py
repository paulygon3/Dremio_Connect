"""
================================================================================
F-21 - Malformed-but-parseable JSON prevents the app from starting   (High)
================================================================================
_load_json (config.py:145-163) catches only json.JSONDecodeError and IOError -
that is, only files that fail to *parse*. Nothing validates the *shape* of what
parsed, and every consumer then assumes dict-of-dicts (config) or list-of-dicts
(history).

Why this is High rather than a curiosity: both failure sites are inside
DremioExporter.__init__ before any window exists.

  config  -> ConfigManager.__init__ -> _merge_with_defaults, called at app.py:54,
             the first statement of the constructor
  history -> get_history_labels, reached from _update_history_dropdown at
             app.py:327, inside _create_query_panel, still during __init__

So the app dies with a traceback and no window ever appears. In a PyInstaller
windowed build there is no console either: the user double-clicks the icon and
nothing happens, forever.

This drives the real ConfigManager through the exact call sequence
DremioExporter.__init__ performs, for each corruption, in a fresh isolated $HOME.

The row to worry about is ["SELECT 1"] - a bare list of query strings is the
obvious v1 history format, so any older build or hand-edit bricks the app.
================================================================================
"""

REQUIRES_DISPLAY = False

import harness as h

CONFIG_CASES = [
    ('{"connection": {"host', "truncated mid-object"),
    ("", "empty file"),
    ("[]", "list where a dict is expected"),
    ("null", "JSON null"),
    ("42", "bare number"),
    ('{"connection": "oops"}', "section is a string"),
    ('{"connection": ["a"]}', "section is a list"),
]

HISTORY_CASES = [
    ('[{"query":', "truncated mid-object"),
    ("{}", "dict where a list is expected"),
    ('{"a": 1}', "dict with entries"),
    ('["SELECT 1"]', "plausible v1 format: bare list of strings"),
    ("[null]", "list containing null"),
    ("null", "JSON null"),
    ("42", "bare number"),
]


def drive_startup(which, contents):
    """
    Reproduce DremioExporter.__init__'s use of ConfigManager against a corrupt file.

    Returns (outcome, detail) where outcome is 'survived' or an exception name.
    """
    h.add_src_to_path()
    from constants import CONFIG_FILENAME, HISTORY_FILENAME

    with h.isolated_home():
        app_dir = h.app_data_dir()
        app_dir.mkdir(parents=True, exist_ok=True)
        name = CONFIG_FILENAME if which == "config" else HISTORY_FILENAME
        (app_dir / name).write_text(contents, encoding="utf-8")

        import importlib
        import config as config_module
        importlib.reload(config_module)

        try:
            # app.py:54 - the first statement of DremioExporter.__init__
            cfg = config_module.ConfigManager()
        except Exception as e:
            return type(e).__name__, str(e)

        try:
            # app.py:327 - _update_history_dropdown, during _create_query_panel
            cfg.get_history_labels()
            # app.py:441-460 - _load_saved_settings reads through config.get
            cfg.get("connection", "hostname", "")
            cfg.get("output", "directory", "")
        except Exception as e:
            return type(e).__name__, str(e)

        # Surviving is necessary but not sufficient: a silent reset to defaults
        # is the other half of this finding (see F-22), so check the app is in a
        # position to tell the user what it discarded.
        warnings_raised = list(getattr(cfg, "load_warnings", []))
        if warnings_raised:
            return "survived", f"reported: {warnings_raised[0]}"
        return "survived", "SILENTLY reset to defaults - user is not told"


def run_group(title, which, cases):
    h.step(title)
    rows = []
    fatal = 0
    silent = 0
    for contents, description in cases:
        outcome, detail = drive_startup(which, contents)
        if outcome != "survived":
            fatal += 1
            result = f"{outcome}: {detail[:55]}"
        else:
            if "SILENTLY" in detail:
                silent += 1
            result = f"survived - {detail[:70]}"
        shown = repr(contents) if len(contents) < 26 else repr(contents[:23]) + "..."
        rows.append([shown, description, result])
    h.table(["contents", "shape", "result"], rows)
    return fatal, silent


def main():
    h.banner("F-21", "Malformed-but-parseable JSON blocks startup")

    h.note("Each case runs against a real ConfigManager in a fresh isolated $HOME,")
    h.note("driven through the exact call sequence DremioExporter.__init__ performs.")

    config_fatal, config_silent = run_group("config.json", "config", CONFIG_CASES)
    history_fatal, history_silent = run_group(
        "query_history.json", "history", HISTORY_CASES)

    h.step("Where these land in startup")
    h.note("ConfigManager() is app.py:54, the first statement of the constructor.")
    h.note("get_history_labels() is reached at app.py:327, still inside __init__.")
    h.note("Both are before root.mainloop(), so no window is ever created.")

    h.step("What recovery requires")
    h.note("Deleting a JSON file from a hidden app-data folder the user-facing")
    h.note("README never mentions. In a windowed build there is no traceback to read.")

    total = config_fatal + history_fatal
    silent = config_silent + history_silent
    cases = len(CONFIG_CASES) + len(HISTORY_CASES)

    if total:
        h.verdict("F-21", h.CONFIRMED,
                  f"{total} of {cases} corruptions are "
                  f"fatal during __init__ ({config_fatal} config, {history_fatal} "
                  f"history), including the plausible v1 format [\"SELECT 1\"]; the "
                  f"survivors reset settings silently")
    elif silent:
        h.verdict("F-21", h.CONFIRMED,
                  f"no corruption is fatal any more, but {silent} of {cases} still "
                  f"reset settings SILENTLY - the app cannot tell the user what it "
                  f"discarded")
    else:
        h.verdict("F-21", h.NOT_REPRODUCIBLE,
                  f"all {cases} corruptions survive startup, and every one is "
                  f"reported rather than silently discarded - the app opens with a "
                  f"window and an explanation instead of dying before one exists")


if __name__ == "__main__":
    main()
