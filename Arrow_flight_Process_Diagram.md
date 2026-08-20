sequenceDiagram
    participant W as Worker thread
    participant C as FlightClient
    participant M as AuthMiddleware
    participant D as Dremio (Flight server)

    Note over W,D: AUTHENTICATION (during connect)
    W->>C: authenticate_basic_token(user, PAT)<br/>headers=[routing-tag]
    C->>M: start_call() → logs "RPC start: HANDSHAKE" (NEW)
    C->>D: Handshake (Basic user:PAT)
    D-->>M: response headers incl. 'authorization: Bearer …'
    M->>M: received_headers() captures token<br/>SILENT if header absent — no longer raises (CHANGED)
    C-->>W: bearer_token
    Note over W: PAT set to None (scrubbed)

    Note over W,D: CONNECTION TEST
    W->>C: get_flight_info("SELECT 1", [bearer_token])
    C->>M: start_call() → logs "RPC start: GET_FLIGHT_INFO"
    C->>D: GetFlightInfo
    D-->>C: FlightInfo (endpoints + tickets)
    W->>C: do_get(ticket) → read_all()
    C->>M: start_call() → logs "RPC start: DO_GET"
    C->>D: DoGet
    D-->>C: record batches → discarded

    Note over W,D: QUERY EXECUTION (per Execute)
    W->>C: get_flight_info(SQL, [bearer_token])
    C->>D: GetFlightInfo (plans the query)
    D-->>C: FlightInfo (endpoint[0].ticket)
    W->>C: do_get(endpoint[0].ticket)
    C->>D: DoGet (opens result stream)

    loop until StopIteration / cancel
        W->>C: reader.read_chunk()
        C->>D: pull next RecordBatch
        D-->>W: RecordBatch (append, count rows)
        Note over W: check cancel_event between batches
    end

    alt cancelled (Stop / Disconnect / close)
        W->>C: reader.cancel() → FlightCancelledError
        Note over W: discard all batches — no partial file
    else exhausted
        W->>W: pa.Table.from_batches(batches, schema)
    end