"""
================================================================================
F-24 - Validation accepts hostnames and ports that cannot connect   (Medium)
F-23 - clean_hostname mangles ordinary inputs                       (Medium)
================================================================================
Both findings are two halves of one defect, so they are checked together.

F-24's sharper half was that the validated value and the used value were
different objects:

    validate_connection_params parsed the port with int(port)
    _connect then passed the RAW STRING through unchanged
    connection.py interpolated it into the URI directly

So '  32010  ' validated as the integer 32010 and then built the location
string "grpc+tls://host:  32010  ". int() also accepts Unicode decimal digits
and a leading '+', neither of which survives URI parsing. Hostname had the same
split - checked only for non-emptiness, then cleaned separately - which is how
'https://' passed validation and produced "grpc+tls://:32010".

F-23 was the cleaning itself: case-sensitive scheme stripping turned
'HTTPS://Dremio.Example.com' into the literal host 'HTTPS', an unconditional
split(':') destroyed every IPv6 literal, and paths were never stripped at all.

The fix makes the two inseparable: validate_connection_params returns the
canonical values it validated, and callers use those. The URI is built from what
was checked, by construction rather than by discipline.

The URI is observed, not inferred - captured through connect()'s own on_status
callback, which reports the location before any socket work.
================================================================================
"""

REQUIRES_DISPLAY = False

import harness as h

# (hostname, port, should_be_accepted)
CASES = [
    ("not a hostname!!", "32010", False),
    ("http://", "32010", False),
    ("';DROP", "32010", False),
    ("x", "  32010  ", True),      # ordinary paste; canonicalises to '32010'
    ("x", "１２３４", False),          # fullwidth digits
    ("x", "+32010", False),
    ("x", "0", False),             # in range for int(), not a usable port
    ("x", "99999", False),
    ("dremio.example.com", "32010", True),
    ("HTTPS://Dremio.Example.com", "32010", True),   # F-23: kept 'HTTPS'
    ("dremio.example.com/api/v3", "32010", True),    # F-23: path survived
    ("dremio.example.com:9047", "32010", True),      # port in both fields
    ("[::1]:32010", "32010", True),                  # F-23: became '['
    ("::1", "32010", True),                          # F-23: became ''
    ("a:b:c", "32010", False),                        # not an address at all
]

# F-23's own table, against clean_hostname directly.
CLEANING = [
    ("dremio.example.com", "dremio.example.com"),
    ("https://dremio.example.com/", "dremio.example.com"),
    ("HTTPS://Dremio.Example.com", "Dremio.Example.com"),
    ("https://", ""),
    ("dremio.example.com/api/v3", "dremio.example.com"),
    ("::1", "[::1]"),
    ("[::1]:32010", "[::1]"),
    ("http://a//b", "a"),
    ("  dremio.example.com:9047  ", "dremio.example.com"),
]


def cleaning_table():
    h.step("F-23: what clean_hostname does to ordinary inputs")
    h.add_src_to_path()
    from utils import clean_hostname

    rows = []
    wrong = []
    for raw, expected in CLEANING:
        got = clean_hostname(raw)
        ok = got == expected
        if not ok:
            wrong.append((raw, expected, got))
        rows.append([repr(raw), repr(got), repr(expected),
                     "ok" if ok else "WRONG"])
    h.table(["input", "cleans to", "should be", ""], rows)
    return wrong


def acceptance_table():
    h.step("F-24: what validate_connection_params accepts")
    h.add_src_to_path()
    from utils import validate_connection_params

    rows = []
    wrong = []
    for hostname, port, should_accept in CASES:
        ok, error, params = validate_connection_params(
            hostname, port, "user", "token")
        if ok != should_accept:
            wrong.append((hostname, port, should_accept, ok, error))
        canonical = (f"{params['hostname']}:{params['port']}" if params
                     else f"rejected: {(error or '').splitlines()[0]}")
        rows.append([repr(hostname), repr(port),
                     "VALID" if ok else "REJECT",
                     "" if ok == should_accept else "UNEXPECTED",
                     canonical])
    h.table(["hostname", "port", "", "", "canonical form / reason"], rows)
    return wrong


def uri_actually_built():
    """
    Capture the URI the real connect() builds, via its own on_status callback.

    connection.py reports "Connecting to: {location}" before the FlightClient is
    constructed, so this observes the real interpolation without needing a
    server to be listening. Every case goes through validation first and uses
    what came back - exactly as _connect now does.
    """
    h.step("The URI built from what validation returned")
    h.add_src_to_path()
    from connection import DremioConnection
    from utils import validate_connection_params

    rows = []
    uris = []
    for hostname, port in [("dremio.example.com", "  32010  "),
                           ("HTTPS://Dremio.Example.com", "32010"),
                           ("dremio.example.com/api/v3", "32010"),
                           ("[::1]:32010", "32010"),
                           ("https://", "32010")]:
        ok, error, params = validate_connection_params(hostname, port, "u", "t")
        if not ok:
            rows.append([repr(hostname), repr(port), "rejected",
                         f"never built: {(error or '').splitlines()[0]}"])
            continue

        messages = []
        conn = DremioConnection()
        try:
            conn.connect(hostname=params['hostname'], port=params['port'],
                         username="u", token="t", use_tls=True,
                         on_status=messages.append)
        except Exception:
            pass
        uri = next((m.split("Connecting to: ", 1)[1] for m in messages
                    if m.startswith("Connecting to: ")), "(not reached)")
        uris.append(uri)
        rows.append([repr(hostname), repr(port), "VALID", repr(uri)])
    h.table(["hostname typed", "port typed", "validation",
             "URI actually built"], rows)
    return uris


def connection_guards():
    """
    connection.py used to hold a third copy of the cleaning rules. It should
    now reject a non-canonical hostname rather than quietly transform it into a
    different wrong one.
    """
    h.step("connection.connect rejects what it will not clean")
    h.add_src_to_path()
    from connection import DremioConnection

    rows = []
    guarded = 0
    for bad in ["https://dremio.example.com", "dremio.example.com/api",
                "has space", ""]:
        conn = DremioConnection()
        try:
            conn.connect(hostname=bad, port="32010", username="u", token="t",
                         use_tls=False, on_status=lambda m: None)
            outcome = "accepted it"
        except ValueError as e:
            guarded += 1
            outcome = f"ValueError: {str(e)[:60]}"
        except Exception as e:
            outcome = f"{type(e).__name__} (got past the guard)"
        rows.append([repr(bad), outcome])
    h.table(["hostname passed directly", "result"], rows)
    return guarded


def is_malformed(uri):
    """
    Is this Flight URI something gRPC could actually dial?

    Only the authority is examined. The scheme is 'grpc+tls', so a naive search
    for '+' or ':' across the whole string calls every URI malformed - which it
    did, and reported the fix as broken while the URIs were all correct.
    """
    if "://" not in uri:
        return True
    authority = uri.split("://", 1)[1]

    if authority.startswith("["):                 # IPv6 literal
        closing = authority.find("]")
        if closing == -1:
            return True
        host = authority[:closing + 1]
        rest = authority[closing + 1:]
        if rest and not rest.startswith(":"):     # ']' not followed by the port
            return True
        port = rest[1:]
    else:
        host, _, port = authority.rpartition(":")

    if not host or not port:
        return True
    if any(c.isspace() for c in authority):
        return True
    if "/" in authority:
        return True
    return not port.isascii() or not port.isdigit()


def passthrough_sites():
    h.step("STATIC: is the raw widget value still handed on?")
    for name, lineno, line in h.grep_source(
            r"args=\(hostname, port|location = f\"|params\['hostname'\]|"
            r"clean_hostname\(", ["app.py", "connection.py"]):
        h.detail(f"{name}:{lineno}", line)


def main():
    h.banner("F-24 / F-23", "Validation, cleaning, and the URI actually built")

    mangled = cleaning_table()
    unexpected = acceptance_table()
    uris = uri_actually_built()
    guarded = connection_guards()
    passthrough_sites()

    malformed = [u for u in uris if is_malformed(u)]

    h.step("Contract check")
    h.detail("F-23: inputs cleaned wrongly",
             f"{len(mangled)} of {len(CLEANING)}"
             + (f" - {mangled}" if mangled else ""))
    h.detail("F-24: inputs classified wrongly",
             f"{len(unexpected)} of {len(CASES)}"
             + (f" - {unexpected}" if unexpected else ""))
    h.detail("URIs built malformed", malformed or "none")
    h.detail("connection.connect guards a non-canonical hostname",
             f"{guarded} of 4")

    if not mangled and not unexpected and not malformed and guarded == 4:
        h.verdict("F-24", h.NOT_REPRODUCIBLE,
                  f"validate_connection_params now returns the canonical hostname "
                  f"and port it validated, and _connect uses those - so every URI "
                  f"built is well-formed, including from '  32010  ', "
                  f"'HTTPS://Host', a pasted path and an IPv6 literal. "
                  f"Unconnectable input is rejected before a socket is opened, and "
                  f"connection.connect refuses a non-canonical hostname rather than "
                  f"re-cleaning it with a fourth copy of the rules")
        h.verdict("F-23", h.NOT_REPRODUCIBLE,
                  f"all {len(CLEANING)} cleaning cases are correct: scheme "
                  f"stripping is case-insensitive and scheme-agnostic, paths and "
                  f"queries are removed, and IPv6 literals survive - bracketed, as "
                  f"a URI requires")
        return

    if mangled:
        h.verdict("F-23", h.CONFIRMED,
                  f"{len(mangled)} of {len(CLEANING)} inputs are still mangled: "
                  f"{mangled}")
    else:
        h.verdict("F-23", h.NOT_REPRODUCIBLE,
                  f"all {len(CLEANING)} cleaning cases are correct")

    if unexpected or malformed or guarded != 4:
        h.verdict("F-24", h.CONFIRMED,
                  f"misclassified={len(unexpected)} malformed_uris={malformed} "
                  f"connection_guards={guarded}/4")
    else:
        h.verdict("F-24", h.NOT_REPRODUCIBLE,
                  "validation and the URI built from it agree")


if __name__ == "__main__":
    main()
