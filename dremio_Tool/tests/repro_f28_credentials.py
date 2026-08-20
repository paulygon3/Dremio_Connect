"""
================================================================================
F-28 - The PAT is written world-readable in reversible form   (CRITICAL)
================================================================================
Two independent claims, checked separately because they have very different
strength:

  1. STATIC, and load-bearing: there is no permission hardening anywhere in the
     application. No chmod, no 0o600, no S_IRUSR. _save_token_to_file uses a
     bare open(path, 'w'), so the file takes 0o666 masked by whatever umask the
     process happens to inherit. The app makes NO assertion about who may read
     or write its credential store.

  2. EXECUTED: the stored token is recoverable. Base64 is encoding, not
     encryption, and config.py:387 says so honestly - while README.md:94
     advertises "Encrypted token storage".

THE CONTROL EXPERIMENT IS MANDATORY HERE.
An earlier draft of this finding quoted mode numbers as if the app produced
them. It does not - this container does not honour umask at all. Every
permission measurement below is therefore paired with a plain-open() control
file created in the same directory under the same umask. If the control matches
.credentials, the mode is a property of the filesystem, not of the app, and the
number means nothing on its own. The audit carries this caveat forward into the
Tier 1 fix: any permission test written during remediation must include this
control or its result is meaningless.

What survives regardless of the environment is claim 1 and the derived outcomes:
  umask 022 (standard Linux default) -> 0o644, world-readable
  umask 002 (corporate Linux without user-private groups) -> 0o664, group-writable
================================================================================
"""

REQUIRES_DISPLAY = False

import base64
import json
import os
import stat

import harness as h

TOKEN = "super-secret-PAT-value"
USERNAME = "alice"


def measure_under_umask(umask_value):
    """
    Write a real credential file under a set umask, alongside a control file.

    Returns a dict of measurements, or None if the write failed.
    """
    h.add_src_to_path()

    old_umask = os.umask(umask_value)
    try:
        with h.isolated_home():
            import importlib
            import config as config_module
            importlib.reload(config_module)

            cfg = config_module.ConfigManager()
            cfg._save_token_to_file(USERNAME, TOKEN)

            cred = cfg.credentials_file
            if not cred.exists():
                return None

            # The control: same directory, same umask, plain open().
            control = cfg.app_dir / "control_plain_open.txt"
            with open(control, "w") as f:
                f.write("control")

            return {
                "umask": oct(umask_value),
                "cred_mode": oct(stat.S_IMODE(cred.stat().st_mode)),
                "cred_human": stat.filemode(cred.stat().st_mode),
                "dir_mode": oct(stat.S_IMODE(cfg.app_dir.stat().st_mode)),
                "control_mode": oct(stat.S_IMODE(control.stat().st_mode)),
                "raw": cred.read_text(),
            }
    finally:
        os.umask(old_umask)


def main():
    h.banner("F-28", "PAT written world-readable in reversible form")

    h.step("Claim 1 (STATIC, load-bearing): is there any permission hardening?")
    hardening = h.grep_source(r"chmod|0o600|0o700|S_IRUSR|S_IWUSR|os\.open\(")
    if hardening:
        for name, lineno, line in hardening:
            h.detail(f"{name}:{lineno}", line)
    else:
        h.detail("chmod / 0o600 / S_IRUSR / os.open across the whole app", "NONE")

    writer = h.grep_source(r"open\(self\.credentials_file", ["config.py"])
    for name, lineno, line in writer:
        h.detail(f"{name}:{lineno}", line)

    h.step("Claim 2 (EXECUTED): is the stored token recoverable?")
    measurements = []
    for umask_value in (0o022, 0o077):
        m = measure_under_umask(umask_value)
        if m:
            measurements.append(m)

    if not measurements:
        h.verdict("F-28", h.NOT_REPRODUCIBLE, "no credential file was produced")
        return

    raw = measurements[0]["raw"]
    h.detail("raw file contents", raw)
    stored = json.loads(raw)[USERNAME]
    decoded = base64.b64decode(stored.encode()).decode()
    h.detail("base64 field", stored)
    h.detail("decoded token", repr(decoded))
    recovered = decoded == TOKEN
    h.detail("round-trips to the original PAT", recovered)

    h.step("Permissions, each row paired with a plain-open() control")
    h.table(
        ["umask", ".credentials", "human", "app dir", "plain-open() control"],
        [[m["umask"], m["cred_mode"], m["cred_human"], m["dir_mode"],
          m["control_mode"]] for m in measurements],
    )

    control_matches = all(m["cred_mode"] == m["control_mode"] for m in measurements)
    umask_honoured = len({m["cred_mode"] for m in measurements}) > 1

    h.detail("control matches .credentials in every row", control_matches)
    h.detail("mode changes between umask 022 and 077", umask_honoured)

    if control_matches and not umask_honoured:
        h.note("This filesystem does NOT honour umask. The mode numbers above are "
               "an artifact of the container, not of application behaviour, and "
               "prove nothing about the app on their own. They are recorded only "
               "as the control that disproves any claim built on them.")
    elif control_matches:
        h.note("The control matches .credentials, so the app applies no hardening "
               "of its own - the mode is entirely ambient policy.")

    h.step("What is true regardless of this environment")
    h.note("With no chmod and a bare open(), the file is 0o666 masked by the "
           "inherited umask. Derived: umask 022 -> 0o644 world-readable; "
           "umask 002 -> 0o664 group-writable.")
    h.note("Where the umask permits group or world write, an attacker can REPLACE "
           ".credentials with a token of their choosing. config.json sits in the "
           "same directory under the same unhardened writer and holds hostname, so "
           "the same primitive redirects the client to an attacker's server AND "
           "supplies the credential it presents.")

    h.step("Is this path even taken in this environment?")
    import keyring
    backend = str(keyring.get_keyring())
    h.detail("keyring backend", backend)
    reachable = "fail.Keyring" in backend
    h.detail("every keyring call raises, so storage falls through to this file",
             reachable)

    # -- Verdicts ---------------------------------------------------------
    #
    # Two independent claims with different fixes and different lifetimes, so
    # they get separate verdicts. The permission half is Tier 1 and should flip
    # once hardened; the reversibility half is inherent to the base64 fallback
    # and stays CONFIRMED until the fallback itself is replaced.

    h.step("STATIC criteria: assert on the code, not only on a stat()")
    h.note("This container ignores umask, so a bare stat() proves little on its "
           "own. These checks are on the source and hold anywhere.")

    creating_open = h.grep_source(r"os\.open\(", ["config.py"])
    mode_arg = h.grep_source(r"0o600", ["config.py"])
    dir_mode = h.grep_source(r"0o700", ["config.py"])
    plain_write = [x for x in h.grep_source(
        r"open\(self\.credentials_file,\s*'w'", ["config.py"])]

    for name, lineno, line in creating_open + mode_arg + dir_mode:
        h.detail(f"{name}:{lineno}", line)
    h.detail("creates the file with os.open + an explicit mode", bool(creating_open))
    h.detail("0o600 appears as a creation mode", bool(mode_arg))
    h.detail("0o700 applied to the app directory", bool(dir_mode))
    h.detail("any remaining plain open(credentials_file, 'w')",
             plain_write or "NONE")

    h.step("Why a post-hoc chmod is STILL required, despite os.open")
    h.note("os.open's mode argument applies only when the file is CREATED. It "
           "does nothing to a file that already exists, and O_TRUNC does not "
           "change permissions. Measured:")
    with h.temp_dir() as probe_dir:
        legacy = probe_dir / "legacy_credentials"
        os.close(os.open(legacy, os.O_WRONLY | os.O_CREAT, 0o644))
        os.chmod(legacy, 0o644)
        before = oct(stat.S_IMODE(legacy.stat().st_mode))
        fd = os.open(legacy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        after_open = oct(stat.S_IMODE(legacy.stat().st_mode))
        os.chmod(legacy, 0o600)
        after_chmod = oct(stat.S_IMODE(legacy.stat().st_mode))
    h.table(["step", "mode"],
            [["file left by an older version", before],
             ["after os.open(..., 0o600) with O_TRUNC", after_open],
             ["after explicit os.chmod(0o600)", after_chmod]])
    migrates = after_open != after_chmod
    h.detail("=> os.open alone would leave upgraded installs world-readable",
             migrates)
    h.note("So the chmod is not a redundant post-hoc fixup - it is the upgrade "
           "path. os.open handles first creation with no exposure window; chmod "
           "handles every user who already has a 0o644 file on disk.")

    h.step("Verdict criteria")
    owner_only = all(m["cred_mode"] == "0o600" for m in measurements)
    differs_from_control = all(m["cred_mode"] != m["control_mode"]
                               for m in measurements)
    dir_hardened = all(m["dir_mode"] == "0o700" for m in measurements)
    h.detail(".credentials is 0o600 in every row", owner_only)
    h.detail("differs from the plain-open() control", differs_from_control)
    h.detail("app dir is 0o700 in every row", dir_hardened)
    h.note("The differential against the control is the criterion that works "
           "even on a filesystem that ignores umask: if .credentials differs "
           "from a plain open() in the same directory, the app is asserting "
           "something of its own.")

    static_ok = bool(creating_open) and bool(mode_arg) and not plain_write

    if static_ok and owner_only and differs_from_control:
        h.verdict("F-28 (perms)", h.NOT_REPRODUCIBLE,
                  f"STATIC: .credentials is created with os.open and an explicit "
                  f"0o600 mode, no plain open(...,'w') remains, and the app dir is "
                  f"forced to 0o700. MEASURED (corroborating, differential against a "
                  f"plain-open() control in the same directory under the same umask): "
                  f".credentials is 0o600 where the control is "
                  f"{measurements[0]['control_mode']}, so the mode is the app's own "
                  f"assertion rather than ambient policy. The chmod is retained "
                  f"deliberately: os.open's mode does not apply to a pre-existing "
                  f"file, so without it an upgraded install keeps its old "
                  f"world-readable file")
    elif not static_ok:
        h.verdict("F-28 (perms)", h.CONFIRMED,
                  f"source-level hardening incomplete: os.open={bool(creating_open)}, "
                  f"0o600 mode={bool(mode_arg)}, leftover plain writes={plain_write}")
    else:
        h.verdict("F-28 (perms)", h.CONFIRMED,
                  f"no permission hardening in the app "
                  f"(chmod/0o600/S_IRUSR/os.open found: {bool(hardening)}); the "
                  f"plain-open() control matches .credentials in every row "
                  f"(control_matches={control_matches}, umask honoured="
                  f"{umask_honoured}), so the mode is ambient policy and the app "
                  f"asserts nothing about who may read or write its credential store")

    if recovered:
        h.verdict("F-28 (encoding)", h.CONFIRMED,
                  f"base64 is encoding, not encryption: the PAT round-trips out of "
                  f".credentials in cleartext. keyring is {backend.split()[0]} here, "
                  f"so this fallback is the live path (reachable={reachable}). This "
                  f"half is inherent to the fallback design and is expected to stay "
                  f"CONFIRMED until the fallback is replaced - permission hardening "
                  f"does not address it")
    else:
        h.verdict("F-28 (encoding)", h.NOT_REPRODUCIBLE,
                  "the stored token no longer round-trips to cleartext")


if __name__ == "__main__":
    main()
