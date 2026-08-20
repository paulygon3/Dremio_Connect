"""
================================================================================
flightserver.py - A local, Dremio-shaped Arrow Flight server for repro tests
================================================================================
Why this exists
---------------
AUDIT.md tagged F-13 (dead Stop button) and F-14 (disconnect during query) as
SOURCE-only, and the audit expected them to stay that way because
reproducing them "needs a display and a live Dremio endpoint". The display half
is solved by Xvfb. This module solves the other half without a real server.

It stands up an in-process pyarrow.flight.FlightServerBase that speaks enough of
Dremio's dialect for the app's own `DremioConnection.connect()` to succeed
unmodified:

    - a ServerAuthHandler, so `authenticate_basic_token` is implemented at all
      (without one the server answers "This service does not have an
      authentication mechanism enabled")
    - a ServerMiddleware that returns an `authorization: Bearer ...` response
      header, which is the exact thing DremioClientAuthMiddleware.received_headers
      (connection.py:48) is written to capture

So the tests exercise the real auth middleware, the real bearer-token replay,
the real `get_flight_info` / `do_get` / `read_all` sequence, and the real
`_arrow_to_pandas` cast.

What this is NOT
----------------
It is not Dremio. Findings whose mechanism depends on Dremio-specific server
behaviour - query planning, server-side cancellation semantics, Dremio's own
error payloads - are not settled by it, and any script relying on that
distinction says so in its verdict note. What it does prove is how *the app*
behaves when a Flight server is slow, which is all F-13 and F-14 turn on.

Delays are deliberate: `info_delay` widens the window between `get_flight_info`
and `do_get` (F-14's race), `batch_delay` makes the stream long enough to press
Stop against (F-13).
================================================================================
"""

import contextlib
import multiprocessing
import threading
import time

import pyarrow as pa
import pyarrow.flight as flight

BEARER = "Bearer local-repro-token"


def string_table(rows, cols):
    """A rows x cols table of distinct strings - the shape F-08 measures."""
    return pa.table({
        f"col_{c}": pa.array([f"value_{c}_{r}" for r in range(rows)])
        for c in range(cols)
    })


class _AuthMiddleware(flight.ServerMiddleware):
    """Attaches the authorization header the app's client middleware expects."""

    def sending_headers(self):
        return {"authorization": BEARER}


class _AuthMiddlewareFactory(flight.ServerMiddlewareFactory):
    def start_call(self, info, headers):
        return _AuthMiddleware()


class _AuthHandler(flight.ServerAuthHandler):
    """
    Minimal handshake implementation.

    Its only job is to make the Handshake RPC exist so that
    `FlightClient.authenticate_basic_token` does not fail outright. Credentials
    are not checked - these tests are about client-side behaviour, and a real
    credential check would add nothing.
    """

    def authenticate(self, outgoing, incoming):
        outgoing.write(BEARER.encode())

    def is_valid(self, token):
        return b"repro-user"


class ReproFlightServer(flight.FlightServerBase):
    """
    Serves one fixed table, optionally slowly.

    Args:
        table: pyarrow.Table to serve. Defaults to a small two-column table.
        info_delay: seconds to sleep inside get_flight_info. Widens the
            disconnect race window for F-14.
        batch_delay: seconds to sleep between record batches in do_get. Makes
            the read long enough to press Stop against for F-13.
        batch_rows: rows per record batch when batch_delay is in play.
    """

    def __init__(self, table=None, info_delay=0.0, batch_delay=0.0,
                 batch_rows=1000):
        super().__init__(flight.Location.for_grpc_tcp("localhost", 0),
                         auth_handler=_AuthHandler(),
                         middleware={"auth": _AuthMiddlewareFactory()})
        if table is None:
            table = pa.table({
                "id": pa.array(range(5000)),
                "name": pa.array([f"row-{i}" for i in range(5000)]),
            })
        self.table = table
        self.info_delay = info_delay
        self.batch_delay = batch_delay
        self.batch_rows = batch_rows

        # Observability for the tests: did the server actually get asked for
        # data, and did it finish sending it?
        self.do_get_started = threading.Event()
        self.do_get_finished = threading.Event()
        self.batches_sent = 0

        self._thread = None

    # -- Flight API --------------------------------------------------------

    def get_flight_info(self, context, descriptor):
        if self.info_delay:
            time.sleep(self.info_delay)
        endpoint = flight.FlightEndpoint(
            b"repro-ticket",
            [flight.Location.for_grpc_tcp("localhost", self.port)],
        )
        return flight.FlightInfo(self.table.schema, descriptor, [endpoint],
                                 self.table.num_rows, self.table.nbytes)

    def do_get(self, context, ticket):
        self.do_get_started.set()
        if not self.batch_delay:
            self.do_get_finished.set()
            return flight.RecordBatchStream(self.table)

        batches = self.table.to_batches(max_chunksize=self.batch_rows)

        def generate():
            for batch in batches:
                time.sleep(self.batch_delay)
                self.batches_sent += 1
                yield batch
            self.do_get_finished.set()

        return flight.GeneratorStream(self.table.schema, generate())

    def reset_counters(self):
        """
        Clear the observability state.

        Call this after connect() and before the call under test. connect()
        runs _test_connection (connection.py:281-294), which performs its own
        get_flight_info + do_get + read_all against this same server - so
        without a reset, do_get_finished is already set and batches_sent already
        counts a whole extra stream before the query has even been sent.
        """
        self.do_get_started.clear()
        self.do_get_finished.clear()
        self.batches_sent = 0

    # -- Lifecycle ---------------------------------------------------------

    def start(self):
        """Serve on a background thread and wait until the port is live."""
        self._thread = threading.Thread(target=self.serve, daemon=True)
        self._thread.start()
        # serve() binds before this returns; a short settle avoids a race on
        # the very first client call.
        time.sleep(0.3)
        return self

    def stop(self):
        try:
            self.shutdown()
        except Exception:
            pass


def _subprocess_target(rows, cols, port_queue):
    """Entry point for the out-of-process server. Must be importable (spawn)."""
    server = ReproFlightServer(table=string_table(rows, cols))
    port_queue.put(server.port)
    server.serve()


@contextlib.contextmanager
def server_subprocess(rows, cols, timeout=60):
    """
    Run the Flight server in a separate process, and yield its port.

    F-08 measures this process's RSS. An in-process server would hold its own
    full copy of the served table inside the very process being measured,
    inflating both the baseline and the concurrent-residency figures by the size
    of the data. A real Dremio is remote, so putting the server in another
    process is the faithful arrangement as well as the clean one.

    'spawn' rather than 'fork': forking a process that has already loaded Tk and
    pyarrow is a good way to get a hang.
    """
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_subprocess_target, args=(rows, cols, queue),
                       daemon=True)
    proc.start()
    try:
        port = queue.get(timeout=timeout)
        time.sleep(0.3)
        yield port
    finally:
        proc.terminate()
        proc.join(timeout=10)


def connected_connection(server_or_port, username="repro-user", token="repro-pat"):
    """
    Return a real DremioConnection connected via the app's real connect().

    Accepts a ReproFlightServer or a bare port number (for the subprocess form).
    Uses connection.py end to end: TLS off, since the local server has no
    certificate, so the code takes the `grpc+tcp` branch at connection.py:242.
    """
    import sys
    from pathlib import Path
    src = str(Path(__file__).resolve().parent.parent)
    if src not in sys.path:
        sys.path.insert(0, src)
    from connection import DremioConnection

    port = server_or_port if isinstance(server_or_port, int) else server_or_port.port
    conn = DremioConnection()
    conn.connect(hostname="localhost", port=str(port),
                 username=username, token=token, use_tls=False)
    return conn
