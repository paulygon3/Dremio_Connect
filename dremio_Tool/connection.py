"""
================================================================================
connection.py - Dremio Connection Manager
================================================================================
Handles Arrow Flight connection to Dremio, including:
    - Authentication middleware
    - SSL certificate handling
    - Connection management
    - Query execution

Based on the proven ArrowFlightModule.py pattern.
================================================================================
"""

import ssl
import codecs
import threading

import pyarrow as pa
from pyarrow import flight

from constants import ROUTING_TAG, SSL_CERT_NAME


class QueryCancelled(Exception):
    """
    Raised when a query read is abandoned because the caller asked it to stop.

    Distinct from a failure on purpose: nothing went wrong, so callers must not
    present it as an error. Whatever rows had arrived are discarded before this
    is raised - a cancelled query yields no partial result.
    """


# =============================================================================
# AUTHENTICATION MIDDLEWARE
# =============================================================================

class DremioClientAuthMiddleware(flight.ClientMiddleware):
    """
    Middleware that captures the bearer token from Dremio's response headers.
    
    When Dremio authenticates a user, it returns a bearer token in the
    'authorization' header. This middleware intercepts that response and
    stores the token in the factory for use in subsequent requests.
    
    This is the CRITICAL piece that makes authentication work - without
    passing the bearer token in FlightCallOptions, queries will fail.
    """

    def __init__(self, factory):
        """
        Initialize middleware with reference to parent factory.
        
        Args:
            factory: DremioClientAuthMiddlewareFactory instance
        """
        self.factory = factory

    def received_headers(self, headers):
        """
        Called when response headers are received from Dremio.
        
        Searches for the 'authorization' header (case-insensitive) and
        stores the bearer token for future requests.
        
        Args:
            headers: Dictionary of response headers
        """
        # Search for authorization header (case-insensitive)
        for key in headers:
            if key.lower() == 'authorization':
                auth_value = headers.get(key)
                if auth_value:
                    self.factory.set_call_credential([
                        b'authorization',
                        auth_value[0].encode('utf-8')
                    ])
                    self.factory._status(
                        "Middleware: captured bearer token from response headers"
                    )
                    return

        # No header on this call. On the initial handshake that would be a real
        # failure - but authenticate_basic_token already surfaces bad
        # credentials on its own - and Dremio does NOT re-echo the header on
        # later RPCs, where the cached credential still authenticates them.
        # Raising here fired on every one of those later calls, flooding stderr
        # with 'Did not receive authorization header back from server' while the
        # queries succeeded anyway. Staying quiet is correct and removes the spam.
        return


class DremioClientAuthMiddlewareFactory(flight.ClientMiddlewareFactory):
    """
    Factory that creates middleware instances and stores credentials.
    
    Arrow Flight calls start_call() before each RPC request, allowing
    the middleware to process each call. The factory stores the bearer
    token that is shared across all requests.
    """

    def __init__(self, on_status=None):
        """Initialize with empty credentials."""
        self.call_credential = []
        # Optional sink for middleware/RPC status lines (set by connect()).
        self.on_status = on_status

    def _status(self, message):
        if self.on_status:
            try:
                self.on_status(message)
            except Exception:
                pass

    def start_call(self, info):
        """
        Create a new middleware instance for each call.
        
        Args:
            info: Flight call info
        
        Returns:
            DremioClientAuthMiddleware: New middleware instance
        """
        method = getattr(info, 'method', None)
        self._status(f"RPC start: {method if method is not None else 'call'}")
        return DremioClientAuthMiddleware(self)

    def set_call_credential(self, call_credential):
        """
        Store the bearer token for future requests.
        
        Args:
            call_credential: List containing [b'authorization', token_bytes]
        """
        self.call_credential = call_credential


# =============================================================================
# SSL CERTIFICATE HANDLING
# =============================================================================

def get_ssl_certificate():
    """
    Extract SSL certificate from Windows certificate store.
    
    Looks for the RWE Server Auth Issuing CA certificate in the
    Windows keychain and returns it in PEM format.
    
    Returns:
        str or None: PEM-formatted certificate, or None if not found
    """
    try:
        # Create SSL context to access Windows cert store
        ssc = ssl.create_default_context()
        ca_cert_list = ssc.get_ca_certs()
        
        # Search for RWE certificate
        for i, cert in enumerate(ca_cert_list):
            try:
                subject = cert.get("subject", [])
                if subject and subject[-1][0][1] == SSL_CERT_NAME:
                    # Found it - extract and convert to PEM
                    cert_bin = ssc.get_ca_certs(True)[i]
                    cert_b64 = "".join(
                        codecs.encode(cert_bin, "base64").decode("utf-8").split()
                    )
                    return f'-----BEGIN CERTIFICATE-----\n{cert_b64}\n-----END CERTIFICATE-----'
            except (IndexError, KeyError, TypeError):
                continue
        
        return None
        
    except Exception as e:
        print(f"Error extracting SSL certificate: {e}")
        return None


# =============================================================================
# CONNECTION MANAGER
# =============================================================================

class DremioConnection:
    """
    Manages connection to Dremio via Arrow Flight protocol.
    
    Usage:
        conn = DremioConnection()
        
        # Connect
        success = conn.connect(
            hostname='dremio.server.com',
            port='32010',
            username='user',
            token='pat_token',
            use_tls=True
        )
        
        # Execute query
        if success:
            df = conn.execute_query('SELECT * FROM table')
        
        # Disconnect
        conn.disconnect()
    
    Attributes:
        is_connected: bool - Current connection state
        client: FlightClient - Arrow Flight client instance
        bearer_token: tuple - Authentication token for requests
    """
    
    def __init__(self):
        """Initialize connection manager."""
        self.client = None
        self.bearer_token = None
        self.middleware = None
        self.is_connected = False
        self.hostname = None
        self.port = None
        # Set while a do_get stream is being read, so cancel_query() - called
        # from the UI thread - can reach into the read the worker is blocked in.
        self._active_reader = None
        self._reader_lock = threading.Lock()

    def connect(self, hostname, port, username, token, use_tls=True,
                on_status=None):
        """
        Connect to Dremio server.
        
        Args:
            hostname: Server hostname (without protocol)
            port: Server port (usually 32010)
            username: Dremio username
            token: Personal Access Token
            use_tls: Whether to use TLS encryption
            on_status: Callback function for status updates (optional)
                       Signature: on_status(message: str)
        
        Returns:
            bool: True if connection successful
        
        Raises:
            Exception: If connection fails
        """
        def status(msg):
            if on_status:
                on_status(msg)
        
        # This used to hold a third copy of utils.clean_hostname's logic, with
        # the same case-sensitive scheme stripping and the same unconditional
        # split(':') that destroyed IPv6 literals (F-23). It is not repeated
        # here: cleaning belongs to the caller, which has already validated the
        # value it is passing, and duplicating the rules is how they drift.
        #
        # Importing utils would be the other way to share them, but utils pulls
        # in tkinter for its asset loaders and this module is deliberately
        # UI-agnostic. So this checks rather than transforms - a wrong value
        # fails here, plainly, instead of being quietly turned into a different
        # wrong value.
        hostname = hostname.strip()
        if not hostname:
            raise ValueError("Hostname is required")
        if '://' in hostname or '/' in hostname:
            raise ValueError(
                f"hostname must be a bare host, with no scheme or path: "
                f"{hostname!r}. Use utils.clean_hostname to normalise it first."
            )
        if any(c.isspace() for c in hostname):
            raise ValueError(f"hostname contains whitespace: {hostname!r}")

        # Connecting while a client already exists replaces it. Dropping the old
        # one rather than closing it is the same leak as disconnect's (F-09),
        # and it is reachable without ever pressing Disconnect: a failed connect
        # leaves a half-built client behind, and the next attempt overwrites it.
        # Any read still running is cancelled first, for the reason disconnect
        # gives - closing a channel out from under a live read faults it.
        if self.client is not None:
            self.cancel_query()
            self._release_client()

        self.hostname = hostname
        self.port = port
        
        # Prepare connection arguments
        connection_args = {}
        
        if use_tls:
            scheme = "grpc+tls"
            status("Checking SSL certificate...")
            
            # Try to get certificate from Windows store
            cert = get_ssl_certificate()
            if cert:
                connection_args["tls_root_certs"] = cert
                status(f"Using {SSL_CERT_NAME} certificate")
            else:
                # Encrypted but UNVERIFIED: without the CA the server's identity
                # cannot be confirmed, so this is open to a man-in-the-middle.
                # The connection still proceeds (a human ticked "Use TLS" and may
                # be on a machine without the corporate CA), but the downgrade is
                # announced loudly rather than buried in a routine status line.
                connection_args["disable_server_verification"] = True
                status(
                    f"WARNING: TLS is on but the {SSL_CERT_NAME} certificate was "
                    f"not found - the connection is ENCRYPTED but the server's "
                    f"identity is NOT verified. Do not use over an untrusted "
                    f"network."
                )
        else:
            scheme = "grpc+tcp"
            status("TLS disabled - using unencrypted connection")
        
        # Create middleware
        status("Creating authentication middleware...")
        self.middleware = DremioClientAuthMiddlewareFactory(on_status=status)
        status("Auth middleware initialised")
        
        # Build connection URI
        location = f"{scheme}://{hostname}:{port}"
        status(f"Connecting to: {location}")
        
        # Create Flight client
        self.client = flight.FlightClient(
            location,
            middleware=[self.middleware],
            **connection_args
        )
        
        # Authenticate
        status("Authenticating...")
        initial_options = flight.FlightCallOptions(
            headers=[(b"routing-tag", ROUTING_TAG)]
        )
        status("RPC: authenticate_basic_token (handshake with routing-tag)")
        
        try:
            self.bearer_token = self.client.authenticate_basic_token(
                username, token, initial_options
            )
        finally:
            # The PAT is not needed past this line - the bearer token replaces
            # it - and this frame can outlive the call: if authentication
            # raises, the traceback holds the frame and the frame holds its
            # locals, so the PAT stays reachable for as long as anything holds
            # the exception (F-30). Scrubbed on both paths.
            token = None
        
        status("Authentication successful!")
        
        # Test connection with simple query
        status("Testing connection...")
        self._test_connection()
        
        self.is_connected = True
        status(f"Connected to {hostname}")
        
        return True
    
    def _test_connection(self):
        """
        Test the connection with a simple query.
        
        Raises:
            Exception: If test query fails
        """
        options = flight.FlightCallOptions(headers=[self.bearer_token])
        info = self.client.get_flight_info(
            flight.FlightDescriptor.for_command("SELECT 1"),
            options
        )
        reader = self.client.do_get(info.endpoints[0].ticket, options)
        try:
            reader.read_all()
        except BaseException:
            # read_all() gave up part-way, so the stream is still open and the
            # server is still sending into it (F-10). Nothing else will close
            # it: FlightStreamReader has no close() and no context manager -
            # cancel() is the only release primitive the type has.
            try:
                reader.cancel()
            except Exception:
                pass
            raise
    
    def _release_client(self):
        """
        Close the gRPC channel behind the current client, then drop it.

        `FlightClient` has both `close()` and the context-manager protocol, and
        the app used neither - `disconnect()` assigned `self.client = None` and
        left the channel and its transport threads for the garbage collector
        (F-09). Over a long session of connect/disconnect cycles that is one
        leaked channel per cycle, freed only if and when GC happens to run.

        `close()` is idempotent, so calling this twice is harmless. Afterwards
        any use of that client raises `ArrowInvalid("FlightClient is closed")`,
        which is what makes the release observable rather than merely asserted.

        Failure to close is deliberately swallowed: this runs on teardown paths
        where the connection is being abandoned anyway, and raising here would
        replace a leaked channel with a leaked channel *and* a broken teardown.
        """
        client = self.client
        self.client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass

    def disconnect(self):
        """
        Disconnect from Dremio server.

        Any read still in flight is cancelled first. Dropping the client while
        a worker is mid-stream used to leave that worker reading from a
        transport nobody owned any more (F-14); cancelling ends the read
        deliberately, and execute_query holds its own reference so it can
        finish unwinding after this returns.

        The client is then closed rather than merely unreferenced (F-09).
        """
        self.cancel_query()
        self._release_client()
        self.bearer_token = None
        self.middleware = None
        self.is_connected = False
        self.hostname = None
        self.port = None
    
    def execute_query(self, query, on_status=None, cancel_event=None):
        """
        Execute a SQL query and return results as DataFrame.

        Args:
            query: SQL query string
            on_status: Callback for status updates
            cancel_event: threading.Event the caller sets to abandon the read.
                Checked between record batches; see cancel_query() for
                interrupting a read that is already blocked.

        Returns:
            pandas.DataFrame: Query results

        Raises:
            QueryCancelled: If the caller asked to stop. No partial result is
                returned - the batches read so far are discarded.
            Exception: If query execution fails
        """
        if not self.is_connected:
            raise Exception("Not connected to Dremio")

        def status(msg):
            if on_status:
                on_status(msg)

        # Bind the client and token once, and use the locals from here on.
        # disconnect() runs on the UI thread and sets self.client = None; this
        # method used to re-read self.client at every step, so a disconnect
        # landing between get_flight_info and do_get produced "'NoneType'
        # object has no attribute 'do_get'" (F-14). Holding a local reference
        # means a disconnect can end this call - through cancel_query - but
        # cannot dismantle it halfway through.
        client = self.client
        bearer_token = self.bearer_token
        if client is None:
            raise Exception("Not connected to Dremio")

        # Create options with bearer token
        options = flight.FlightCallOptions(headers=[bearer_token])

        # Get flight info
        status("Sending query to Dremio...")
        info = client.get_flight_info(
            flight.FlightDescriptor.for_command(query),
            options
        )

        if cancel_event is not None and cancel_event.is_set():
            raise QueryCancelled("Cancelled before the result stream was opened")

        # Fetch data
        status("Retrieving data...")
        reader = client.do_get(info.endpoints[0].ticket, options)
        with self._reader_lock:
            self._active_reader = reader
        try:
            table = self._read_stream(reader, status, cancel_event)
        finally:
            with self._reader_lock:
                self._active_reader = None

        # Convert to DataFrame
        status("Converting data...")
        df = self._arrow_to_pandas(table)

        return df

    def _read_stream(self, reader, status, cancel_event):
        """
        Read the result stream one record batch at a time.

        read_all() is a single blocking call: once it starts there is no point
        at which a cancellation flag can be observed, which is why the Stop
        button had nothing to read (F-13). Reading chunk by chunk gives the flag
        a place to be checked, and gives cancel_query() a live reader to
        interrupt.

        Cancellation discards everything read so far. Handing back a partial
        result would be worse than handing back none: an Excel file containing
        the first N rows of a cancelled query is indistinguishable from a
        complete one.

        Raises:
            QueryCancelled: if cancel_event was set, or if the read was
                interrupted by cancel_query().
        """
        batches = []
        rows = 0
        cancelled = False
        exhausted = False
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break

                try:
                    chunk = reader.read_chunk()
                except StopIteration:
                    exhausted = True
                    break
                except flight.FlightCancelledError:
                    # cancel_query() reached the reader while it was blocked
                    # here. That is the only thing that cancels this call, so
                    # it is a cancellation rather than a failure.
                    cancelled = True
                    break

                if chunk.data is None:
                    continue

                batches.append(chunk.data)
                rows += chunk.data.num_rows
                status(f"Retrieving data... {rows:,} rows")

            if cancelled:
                raise QueryCancelled(
                    f"Cancelled after {rows:,} rows; nothing was kept"
                )

            return pa.Table.from_batches(batches, reader.schema)
        finally:
            # Drop the references on every path. On the cancelled path this is
            # the discard; on the success path it releases the list's hold so
            # the Table is the only owner.
            batches.clear()

            # Release the stream unless it ended on its own. This used to fire
            # only on the cancelled path, which left the whole error path
            # leaking (F-10): if read_chunk() raised a mid-stream Flight error,
            # or the status callback raised, the reader was abandoned with the
            # server still sending into it. Measured against the local Flight
            # server, an abandoned stream kept delivering batches for as long as
            # the test watched; a cancelled one stopped after one.
            #
            # `exhausted` rather than `not cancelled` because from_batches()
            # runs inside the try: if that raises there is nothing left to
            # release, and cancelling a finished stream is pointless rather than
            # harmful.
            if not exhausted:
                # Safe from this thread, and safe to call after the stream has
                # already ended.
                try:
                    reader.cancel()
                except Exception:
                    pass

    def cancel_query(self):
        """
        Interrupt the result stream currently being read, if there is one.

        Called from the UI thread while a worker is blocked inside read_chunk().
        pyarrow releases the GIL for that read and FlightStreamReader.cancel()
        cancels the underlying gRPC call, so the blocked read raises
        FlightCancelledError immediately rather than at the next batch boundary.

        What this cancels is the *Flight result stream*. Whether Dremio also
        stops executing the underlying job is a server-side question this code
        cannot answer or guarantee; see the note in
        tests/repro_f13_stop_button.py.

        Returns:
            bool: True if a live read was signalled, False if none was running.
        """
        with self._reader_lock:
            reader = self._active_reader
        if reader is None:
            return False
        try:
            reader.cancel()
            return True
        except Exception:
            return False


    def _arrow_to_pandas(self, table):
        """
        Convert Arrow Table to pandas DataFrame.
        
        Handles decimal128 columns by converting to float64.
        
        Args:
            table: pyarrow.Table
        
        Returns:
            pandas.DataFrame
        """
        # Build new schema with decimal128 converted to float64
        schema_fields = []
        for field in table.schema:
            if pa.types.is_decimal128(field.type):
                schema_fields.append(
                    pa.field(field.name, pa.float64(), field.nullable)
                )
            else:
                schema_fields.append(field)
        
        new_schema = pa.schema(schema_fields)
        
        # Cast and convert
        return table.cast(new_schema).to_pandas(
            split_blocks=True,
            self_destruct=True,
            date_as_object=False
        )
    
    @property
    def connection_string(self):
        """Get human-readable connection string."""
        if self.is_connected:
            return f"{self.hostname}:{self.port}"
        return None
