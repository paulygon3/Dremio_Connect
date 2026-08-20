flowchart TD
    subgraph SRC["Credential sources"]
        KR[("Windows Credential Manager<br/>keyring: DremioExporter/username")]
        CR[(".credentials<br/>base64 fallback")]
        CFG[("config.json<br/>host · port · user · log_retention_days")]
    end

    KR -.token.-> FORM
    CR -.token.-> FORM
    CFG -.host/user.-> FORM
    FORM["Connection form fields"] --> CN["_connect()<br/>validate_connection_params<br/>logs 'Connect requested'"]

    subgraph CONNECT["CONNECT (daemon thread: _connect_thread)"]
        CN --> DC["DremioConnection.connect()"]
        DC --> SSL{"use_tls?"}
        SSL -->|no| PLAIN["grpc+tcp (unencrypted)"]
        SSL -->|yes| CERT{"RWE CA found in<br/>Windows cert store?"}
        CERT -->|yes| VER["tls_root_certs = cert<br/>(server verified)"]
        CERT -->|no| WARN["disable_server_verification<br/>⚠ WARNING logged — encrypted<br/>but NOT verified (CHANGED)"]
        VER --> FC["build flight.FlightClient<br/>+ AuthMiddlewareFactory(on_status)"]
        WARN --> FC
        PLAIN --> FC
        FC --> AUTH["authenticate_basic_token(user, PAT)"]
        AUTH --> MW["middleware captures 'authorization'<br/>→ bearer_token<br/>(silent if absent — no longer raises)"]
        MW --> SCRUB["PAT scrubbed: token = None"]
        SCRUB --> TEST["_test_connection(): SELECT 1"]
    end

    TEST --> READY(["Connected · Execute enabled<br/>'Connection established in Xs'"])

    READY --> EX["_execute_and_export()<br/>resolve path · overwrite prompt<br/>snapshot settings · logs query"]

    subgraph EXEC["EXECUTE (daemon thread: _execute_thread)"]
        EX --> EQ["connection.execute_query(query, cancel_event)"]
        EQ --> FLIGHT[["Arrow Flight exchange<br/>(see sequence diagram)"]]
        FLIGHT --> TBL["pa.Table.from_batches(...)"]
        TBL --> A2P["_arrow_to_pandas()<br/>decimal128 → float64<br/>to_pandas()"]
        A2P --> DF[("pandas DataFrame")]
    end

    DF --> XL

    subgraph EXPORT["EXPORT (_export_to_excel)"]
        XL["openpyxl Workbook(write_only)"] --> ROWS{"rows > 1,048,575?"}
        ROWS -->|yes| REFUSE["Refuse before writing<br/>(row ceiling)"]
        ROWS -->|no| COLS{"cols > 16,384?"}
        COLS -->|yes| REFUSE2["Refuse before writing<br/>(column ceiling — NEW)"]
        COLS -->|no| LOOP["append loop, cell by cell"]
        LOOP --> SAN["sanitise illegal control chars"]
        LOOP --> TRUNC{"cell > 32,767 chars?"}
        TRUNC -->|yes| SIDE["write full values to<br/>&lt;name&gt;.truncated.txt sidecar"]
        SAN --> WRITE["atomic write:<br/>temp file → fsync → os.replace"]
        TRUNC --> WRITE
        WRITE --> XLSX[("export.xlsx")]
    end

    XLSX --> DONE(["Report warnings · success dialog<br/>open file (Windows)"])

    LOG[["Session .txt log + panel (NEW)<br/>%APPDATA%…/logs/dremio_log_*.txt<br/>middleware · RPC · timing"]]
    CN -.status.-> LOG
    EQ -.status.-> LOG