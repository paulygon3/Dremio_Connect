"""
================================================================================
F-29 - Unchecking "Remember token" never deletes the stored token   (High)
================================================================================
SOURCE-only in AUDIT.md.

    if self.remember_token.get():          app.py:487
        ...
        self.config.save_token(username, token)
    # no else branch

_save_current_settings writes the token when the box is checked and does nothing
at all when it is unchecked. ConfigManager.delete_token exists (config.py:342)
and is never called from anywhere (proven dead in F-31).

So the sequence a security-conscious user would actually perform:

    1. connect with "Remember token" checked - the default (app.py:245); the
       PAT is written to .credentials
    2. later, uncheck the box to stop storing it
    3. close the app

leaves the PAT on disk unchanged. Worse, _on_username_change (app.py:495) reads
it straight back out of storage and repopulates the masked field, so the UI
actively suggests the credential was retained on purpose.

The only control the UI offers for forgetting a credential does not forget it.
================================================================================
"""

REQUIRES_DISPLAY = True

import harness as h

USERNAME = "alice"
TOKEN = "super-secret-PAT-value"


def main():
    h.require_display()
    h.banner("F-29", "Unchecking 'Remember token' leaves the PAT on disk")

    h.step("STATIC: is there an else branch, and is delete_token ever called?")
    for name, lineno, line in h.grep_source(
            r"remember_token|save_token|delete_token", ["app.py"]):
        h.detail(f"{name}:{lineno}", line)
    callers = [x for x in h.grep_source(r"delete_token", ["app.py"])]
    h.detail("calls to delete_token from the UI", callers or "NONE")

    with h.isolated_home(), h.temp_dir() as tmp:
        with h.tk_app(output_dir=tmp, remember_token=True) as app:
            credentials = app.config.credentials_file

            h.step("Step 1: connect with 'Remember token' checked (the default)")
            h.detail("remember_token default", app.remember_token.get())
            h.set_entry(app.conn_fields["username"], USERNAME)
            h.set_entry(app.conn_fields["token"], TOKEN)
            app._save_current_settings()

            stored_after_save = app.config.get_token(USERNAME)
            h.detail(".credentials exists", credentials.exists())
            h.detail("token retrievable", stored_after_save == TOKEN)

            h.step("Step 2: uncheck the box to stop storing it")
            app.remember_token.set(False)
            h.detail("remember_token now", app.remember_token.get())
            app._save_current_settings()

            h.step("Step 3: close the app (_on_close -> _save_current_settings)")
            # _on_close also calls root.destroy(); call the settings half only so
            # the widgets survive for the checks below.
            app._save_current_settings()

            still_stored = app.config.get_token(USERNAME)
            h.detail(".credentials still exists", credentials.exists())
            h.detail("token still retrievable", still_stored == TOKEN)
            h.detail("raw file contents",
                     credentials.read_text() if credentials.exists() else "(gone)")

            h.step("What the UI does on the next launch")
            app.conn_fields["token"].delete(0, "end")
            h.detail("token field cleared", repr(app.conn_fields["token"].get()))
            app._on_username_change()
            repopulated = app.conn_fields["token"].get()
            h.detail("after _on_username_change, token field",
                     "REPOPULATED from storage" if repopulated == TOKEN
                     else repr(repopulated))

            h.step("Can the credential be removed through the app at all?")
            if callers:
                for name, lineno, line in callers:
                    h.detail(f"delete_token called from {name}:{lineno}", line)
            else:
                h.note("delete_token exists at config.py:342 and has no caller. "
                       "Short of hand-deleting a hidden file, the credential "
                       "cannot be removed through the app at all.")

    leaked = still_stored == TOKEN
    if leaked and not callers:
        h.verdict("F-29", h.CONFIRMED,
                  f"unchecking 'Remember token' and saving twice leaves the PAT in "
                  f".credentials unchanged and still retrievable; delete_token has no "
                  f"caller anywhere in the UI; and _on_username_change repopulates the "
                  f"masked field from storage "
                  f"({'repopulated' if repopulated == TOKEN else 'not repopulated'}), "
                  f"so the UI suggests the retention was deliberate")
    elif callers:
        h.verdict("F-29", h.NOT_REPRODUCIBLE,
                  f"delete_token now has callers: {callers}")
    else:
        h.verdict("F-29", h.NOT_REPRODUCIBLE,
                  "the token was removed when the box was unchecked")


if __name__ == "__main__":
    main()
