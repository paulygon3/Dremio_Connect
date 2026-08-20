"""
================================================================================
F-09 - FlightClient is never closed; disconnect() only drops the reference (Med)
F-10 - Flight readers leak on the error path                            (Medium)
================================================================================
One defect in two places: pyarrow hands out a release primitive and the app
never reaches for it.

    FlightClient        has close() and __enter__/__exit__   - neither was used
    FlightStreamReader  has cancel() and nothing else        - it was used only
                                                               on the cancelled
                                                               path

F-09: `disconnect()` set `self.client = None` and stopped there, leaving the
gRPC channel and its transport threads for the garbage collector. One leaked
channel per connect/disconnect cycle, freed if and when GC happens to run.

F-10: `reader = do_get(...)` followed by an unguarded read. If the read gives up
part-way the reader is abandoned with the stream still open and the server still
sending into it. `_read_stream` released the reader only when `cancelled` was
set, so every *error* path leaked; `_test_connection` had no guard at all.

Neither is asserted from source here. Both are measured:

  - a closed client is observable, because pyarrow raises
    ArrowInvalid("FlightClient is closed") on any subsequent use
  - a leaked reader is observable from the server side, because a stream nobody
    is reading and nobody cancelled keeps on delivering batches

The F-10 trigger is a real path rather than a synthetic one: the status callback
raising mid-read. `execute_query` calls it once per batch, and in the app it
ends up marshalling into Tk - so it is exactly the kind of caller-supplied code
that can fail while the stream is half-read.

A correction to F-10, found by running it
-----------------------------------------
AUDIT.md says the abandoned reader is "left for the garbage collector". True in
mechanism, but the practical consequence is smaller and stranger than that, and
the first version of this script missed the bug entirely because of it: measured
against the UNFIXED code, the leak only appears when the caller keeps the
exception. Hold it and the server delivers ~29 more batches into a stream nobody
is reading; drop it and CPython frees the frames - and with them the reader -
the instant the handler ends, so the destructor releases the stream and the
delta is 1. Refcounting, not the cyclic collector.

So unfixed, release was *incidental*: correct only while no error handler
retains a traceback. app.py:1096 binds `str(e)` and does not keep `e`, so the
shipped app was on the lucky side of that - which makes F-10 latent rather than
active, and the fix a determinism fix rather than the plugging of an observed
unbounded leak. Both measurements are reported below; a script that took only
the first would call a leaking build clean.
================================================================================
"""

REQUIRES_DISPLAY = False

import time

import pyarrow.flight as flight

import harness as h
import flightserver as fs

# Enough batches that a leaked stream has room to keep going visibly, and a
# delay slow enough that the difference is a count rather than a photo finish.
BATCH_ROWS = 200
BATCH_DELAY = 0.05
ROWS = 8000

# How long to watch the server after the client has given up.
WATCH = 1.5


def client_is_closed(client, bearer_token):
    """
    Did close() actually happen? Ask the client, do not grep for the call.

    pyarrow raises ArrowInvalid("FlightClient is closed") on any use of a closed
    client, so a rejected call is proof the channel was released rather than
    merely dereferenced.

    The bearer token matters: without it the auth middleware rejects the call
    before it leaves the process, so a perfectly healthy client also raises -
    and "closed" and "usable" stop being distinguishable. Passing the token the
    connection captured means a live client genuinely completes the RPC.
    """
    try:
        options = flight.FlightCallOptions(headers=[bearer_token])
        client.get_flight_info(
            flight.FlightDescriptor.for_command("SELECT 1"), options)
        return False, "still served a call"
    except Exception as e:
        if "closed" in str(e).lower():
            return True, f"{type(e).__name__}: {str(e)[:40]}"
        return False, f"{type(e).__name__}: {str(e)[:40]}"


def disconnect_closes_the_client():
    h.step("F-09: what disconnect() does to the channel")
    h.add_src_to_path()

    rows = []
    released = 0

    server = fs.ReproFlightServer().start()
    try:
        # Cycle 1: connect -> disconnect. The client the app built is captured
        # before disconnect drops it, so it can be interrogated afterwards.
        conn = fs.connected_connection(server)
        first, first_token = conn.client, conn.bearer_token
        conn.disconnect()
        ok, how = client_is_closed(first, first_token)
        released += ok
        rows.append(["connect -> disconnect", "closed" if ok else "LEAKED", how])

        # Cycle 2: connect -> connect. Reachable without ever pressing
        # Disconnect - a failed attempt leaves a half-built client that the next
        # attempt overwrites.
        conn.connect(hostname="localhost", port=str(server.port),
                     username="repro-user", token="repro-pat", use_tls=False)
        second, second_token = conn.client, conn.bearer_token
        conn.connect(hostname="localhost", port=str(server.port),
                     username="repro-user", token="repro-pat", use_tls=False)
        ok, how = client_is_closed(second, second_token)
        released += ok
        rows.append(["connect -> connect (replaced)",
                     "closed" if ok else "LEAKED", how])

        # The replacement must still be usable - releasing the old channel is
        # only correct if it did not take the new connection with it. This is
        # the case that needs a real completed RPC rather than "raised
        # something", so it is checked against its own token.
        closed, how = client_is_closed(conn.client, conn.bearer_token)
        still_works = not closed and how == "still served a call"
        rows.append(["the replacement client",
                     "usable" if still_works else "BROKEN", how])

        conn.disconnect()
        rows.append(["disconnect twice", "no error"
                     if _second_disconnect_ok(conn) else "RAISED",
                     "close() is idempotent"])
    finally:
        server.stop()

    h.table(["cycle", "outcome", "evidence"], rows)
    return released, still_works


def _second_disconnect_ok(conn):
    try:
        conn.disconnect()
        return True
    except Exception:
        return False


def _read_until_status_raises(conn, server, fail_at, retain_exception):
    """
    Drive a real execute_query whose status callback raises mid-stream.

    `retain_exception` decides whether the caller keeps the exception object
    after catching it. That sounds incidental and is the whole point: holding
    the exception holds `e.__traceback__`, which holds the frames of
    execute_query and _read_stream, which hold `reader`. Drop it and CPython's
    refcounting frees the reader the moment the handler ends, and its destructor
    releases the stream - hiding the missing cancel() entirely.

    Returns (batches_sent_when_it_gave_up, batches_sent_after_watching).
    """
    calls = {"n": 0}

    def exploding_status(_msg):
        calls["n"] += 1
        if calls["n"] >= fail_at:
            raise RuntimeError("status callback failed mid-read")

    server.reset_counters()
    held = []
    try:
        conn.execute_query("SELECT 1", on_status=exploding_status)
    except Exception as e:
        if retain_exception:
            held.append(e)

    at_giveup = server.batches_sent
    time.sleep(WATCH)
    after = server.batches_sent
    held.clear()
    return at_giveup, after


def reader_released_on_the_error_path():
    """
    Measure the error path twice, differing only in whether the traceback lives.

    The two rows are the finding. A single measurement here is worse than none:
    take only the first and the leak is invisible even in code that has it.
    """
    h.step("F-10: what the server keeps doing after the read gives up")
    h.add_src_to_path()

    rows = []
    deltas = {}
    for label, retain in [("traceback dropped at once", False),
                          ("traceback retained by the handler", True)]:
        server = fs.ReproFlightServer(
            table=fs.string_table(ROWS, 2),
            batch_delay=BATCH_DELAY,
            batch_rows=BATCH_ROWS,
        ).start()
        try:
            conn = fs.connected_connection(server)
            # The status callback fires for the query steps as well as per
            # batch; failing on the 5th call lands inside the stream.
            at_giveup, after = _read_until_status_raises(
                conn, server, fail_at=5, retain_exception=retain)
            conn.disconnect()
        finally:
            server.stop()

        deltas[retain] = after - at_giveup
        rows.append([label, str(at_giveup), str(after), str(after - at_giveup)])

    h.table(["after the read gave up", "batches then",
             f"batches {WATCH}s later", "delivered to nobody"], rows)
    return deltas[False], deltas[True]


def control_measurement():
    """
    What a genuinely leaked stream looks like on this machine.

    Without this the F-10 number means nothing: a small delta could equally mean
    "released promptly" or "the server was slow". This abandons a reader
    deliberately - no cancel() - and measures the same window, so the two
    figures are directly comparable.
    """
    h.step("Control: the same read, abandoned without cancel()")
    h.add_src_to_path()

    server = fs.ReproFlightServer(
        table=fs.string_table(ROWS, 2),
        batch_delay=BATCH_DELAY,
        batch_rows=BATCH_ROWS,
    ).start()
    try:
        conn = fs.connected_connection(server)
        server.reset_counters()
        options = flight.FlightCallOptions(headers=[conn.bearer_token])
        info = conn.client.get_flight_info(
            flight.FlightDescriptor.for_command("SELECT 1"), options)
        reader = conn.client.do_get(info.endpoints[0].ticket, options)
        for _ in range(3):
            reader.read_chunk()
        at_giveup = server.batches_sent
        # Deliberately no reader.cancel() - this is the leak.
        time.sleep(WATCH)
        after = server.batches_sent
        del reader
        conn.disconnect()
    finally:
        server.stop()

    delta = after - at_giveup
    h.table(["measurement", "batches"],
            [["sent when the reader was abandoned", str(at_giveup)],
             [f"sent {WATCH}s later", str(after)],
             ["delivered into an abandoned stream", str(delta)]])
    return delta


def release_sites():
    h.step("STATIC: every place a reader or client is obtained")
    for name, lineno, line in h.grep_source(
            r"do_get\(|read_all\(|\.close\(\)|reader\.cancel\(\)|_release_client",
            ["connection.py"]):
        h.detail(f"{name}:{lineno}", line)


def main():
    h.banner("F-09 / F-10", "Channels and streams, and whether they are released")

    released, replacement_ok = disconnect_closes_the_client()
    dropped_delta, retained_delta = reader_released_on_the_error_path()
    control_delta = control_measurement()
    release_sites()

    h.step("Contract check")
    h.detail("F-09: clients closed rather than dropped", f"{released} of 2")
    h.detail("F-09: the replacement connection still works", replacement_ok)
    h.detail("F-10: batches after the read gave up, traceback dropped",
             dropped_delta)
    h.detail("F-10: batches after the read gave up, traceback retained",
             retained_delta)
    h.detail("F-10: batches after an abandoned read (control)", control_delta)

    # The control is what makes the other numbers mean anything: it is a stream
    # that certainly leaked, measured in the same window on the same machine.
    # Without it a small delta is indistinguishable from a slow server.
    control_leaks = control_delta >= 5
    released_promptly = retained_delta <= max(2, control_delta // 4)

    if not control_leaks:
        h.verdict("F-10", h.BLOCKED,
                  f"the control did not leak either ({control_delta} batches "
                  f"after abandoning a reader with no cancel), so this machine "
                  f"cannot tell a released stream from a leaked one and the "
                  f"measurement is not evidence")
    elif released_promptly:
        h.verdict("F-10", h.NOT_REPRODUCIBLE,
                  f"the error path releases the stream deterministically: "
                  f"{retained_delta} further batches even when the handler "
                  f"retains the traceback, against {control_delta} for a reader "
                  f"abandoned without cancel(). _read_stream now cancels unless "
                  f"the stream ended on its own, and _test_connection's "
                  f"read_all() is guarded too. NOTE the audit overstates this "
                  f"one: unfixed, the same path leaks only when something holds "
                  f"the exception ({control_delta}-batch scale) - drop the "
                  f"traceback and CPython refcounting frees the reader at once "
                  f"({dropped_delta}), so release was incidental rather than "
                  f"absent. app.py binds str(e) and does not retain e, so this "
                  f"was latent in the shipped app; the fix makes release "
                  f"deliberate instead of dependent on refcount timing")
    else:
        h.verdict("F-10", h.CONFIRMED,
                  f"the stream is still leaked on the error path: "
                  f"{retained_delta} batches delivered after the read gave up "
                  f"with the traceback retained, against a control of "
                  f"{control_delta} and {dropped_delta} when the traceback is "
                  f"dropped")

    if released == 2 and replacement_ok:
        h.verdict("F-09", h.NOT_REPRODUCIBLE,
                  "both replacement paths close the channel rather than "
                  "dropping it - disconnect(), and a connect() over an existing "
                  "client - and the closed client proves it by refusing further "
                  "calls. The new connection is unaffected, and closing twice "
                  "is harmless")
    else:
        h.verdict("F-09", h.CONFIRMED,
                  f"{2 - released} of 2 replacement paths left the channel open "
                  f"(replacement usable: {replacement_ok})")


if __name__ == "__main__":
    main()
