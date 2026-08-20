"""
================================================================================
constants.py - Application Constants
================================================================================
Central location for all application constants, colors, and default values.
Modify these to customize the application appearance and behavior.
================================================================================
"""

# =============================================================================
# APPLICATION METADATA
# =============================================================================

APP_NAME = "DremioExporter"
APP_VERSION = "3.0.0"
APP_TITLE = "Dremio to Excel Exporter"
APP_SUBTITLE = "RWE Transmission Team"
COPYRIGHT = f"© 2026 RWE Transmission Team  |  v{APP_VERSION}"


# =============================================================================
# WINDOW SETTINGS
# =============================================================================

WINDOW_MIN_WIDTH = 1000
WINDOW_MIN_HEIGHT = 700
WINDOW_DEFAULT_WIDTH = 1100
WINDOW_DEFAULT_HEIGHT = 800


# =============================================================================
# COLOR SCHEME
# =============================================================================

COLORS = {
    # Header
    'header_bg': '#0066B3',          # RWE Blue
    'header_fg': '#FFFFFF',          # White
    'header_subtitle': '#B0D4F1',    # Light blue
    
    # Connection status
    'connected': '#28A745',          # Green
    'disconnected': '#DC3545',       # Red
    'warning': '#FFC107',            # Yellow
    
    # Buttons
    'button_primary': '#0066B3',     # RWE Blue - Connect
    'button_success': '#28A745',     # Green - Execute
    'button_danger': '#DC3545',      # Red - Stop/Disconnect
    'button_secondary': '#6C757D',   # Gray - Disabled
    
    # Backgrounds
    'log_bg': '#1E1E1E',             # Dark - Log area
    'log_fg': '#CCCCCC',             # Light gray - Log text
    'frame_bg': '#F5F5F5',           # Light gray - Main background
    'white': '#FFFFFF',
    
    # Text
    'text_muted': '#6C757D',         # Gray - Secondary text
    'text_primary': '#333333',       # Dark - Primary text
    
    # Borders
    'border': '#D0D0D0',             # Light gray border
    'border_focus': '#0066B3',       # Blue border on focus
}


# =============================================================================
# DEFAULT CONFIGURATION VALUES
# =============================================================================

# Schema version of config.json, stamped under the 'meta' section. A file
# without it predates versioning and is migrated on load - see
# ConfigManager._migrate_config. Bump this when adding a migration.
CONFIG_VERSION = 1

# The value DEFAULT_CONFIG carried for output.sheet_name before F-07 made the
# setting real. It was written to every config.json and never once read, so a
# stored copy of it is the old default rather than anybody's choice - which is
# what makes it safe to migrate away from.
LEGACY_SHEET_NAME = 'Dremio Data'

# Days to keep per-session .txt log files before old ones are pruned on
# startup. 0 disables pruning (keep forever). Surfaced as a saved GUI setting.
DEFAULT_LOG_RETENTION_DAYS = 30

DEFAULT_CONFIG = {
    'meta': {
        'config_version': CONFIG_VERSION,
    },
    'connection': {
        'hostname': '',
        'port': '32010',
        'username': '',
        'use_tls': True,
        'auth_method': 'pat'
    },
    'output': {
        'directory': '',  # Will be set to Documents/Dremio_Exports
        'filename_pattern': 'dremio_export_{timestamp}.xlsx',
        # 'Data', not 'Dremio Data'. This key has been here from the start and
        # was never read - the writer used the literal 'Data' - so every
        # workbook this app has ever produced has a sheet called 'Data'. Now
        # that the setting is honoured (F-07), the shipped default has to be
        # what those exports actually contain, or wiring it up would silently
        # rename the sheet for everyone and break any formula, Power Query or
        # macro that refers to it by name.
        'sheet_name': 'Data',
        'include_timestamp': True,
        'autofit_columns': True,
        'freeze_header': True,
        'apply_table_format': True,
        'table_style': 'TableStyleMedium2',
        'open_after_export': True
    },
    'ui': {
        'window_width': WINDOW_DEFAULT_WIDTH,
        'window_height': WINDOW_DEFAULT_HEIGHT,
        'last_query': ''
    },
    'logging': {
        # Days to keep per-session log files; 0 keeps them forever.
        'log_retention_days': DEFAULT_LOG_RETENTION_DAYS
    }
}


# =============================================================================
# EXCEL LIMITS
# =============================================================================

# Excel's hard per-cell character limit. openpyxl does not raise when a value
# exceeds it - it emits a UserWarning and writes the truncated string - so the
# export path detects this itself and preserves the full values in a sidecar
# file rather than losing them silently.
EXCEL_MAX_CELL_CHARS = 32767

# Excel's sheet row ceiling, and the number of DATA rows that leaves. The header
# occupies sheet row 1, so data runs from row 2 - one row fewer than the ceiling
# suggests. openpyxl enforces this at cell-construction time, part-way through
# the write, with a ValueError that says only "Row numbers must be between 1 and
# 1048576" and mentions neither Excel nor the query that produced the rows
# (F-04). The export checks the count itself, before writing anything.
EXCEL_MAX_SHEET_ROWS = 1048576
EXCEL_MAX_DATA_ROWS = EXCEL_MAX_SHEET_ROWS - 1

# Excel's worksheet column ceiling, and therefore the most columns a result can
# have. Unlike rows, no header line is consumed - every column carries data - so
# the data limit equals the sheet limit. openpyxl does not stop at it: in
# write_only mode it appends however many cells a row holds, and
# get_column_letter keeps returning valid references well past it (up to column
# ZZZ / 18,278), so a frame wider than this produces a workbook Excel silently
# repairs or refuses to open, after the export has reported success. Checked
# before the write, exactly like the row ceiling.
EXCEL_MAX_COLUMNS = 16384

# Excel's worksheet-name rules (F-07). openpyxl enforces only part of this:
# measured against 25.0.1 it rejects an empty name and the six forbidden
# characters, but ACCEPTS a name over 31 characters (warning to a stderr a
# windowed build does not have), a leading or trailing apostrophe, and the
# reserved name 'History'. Those three produce a workbook Excel refuses to open
# or silently repairs, after the export has reported success - so they are
# checked here rather than left to the library.
EXCEL_MAX_SHEET_NAME = 31
EXCEL_FORBIDDEN_SHEET_CHARS = ('[', ']', ':', '*', '?', '/', '\\')
EXCEL_RESERVED_SHEET_NAME = 'History'

# Used when the configured sheet name cannot be used. A bad value in config.json
# must not fail an export that is otherwise fine - the user is told and this is
# written instead.
DEFAULT_SHEET_NAME = 'Data'

# How many affected cells to name in the dialog and log before summarising.
# The sidecar file always contains every one of them.
TRUNCATION_REPORT_LIMIT = 10

# What replaces a control character openpyxl refuses (F-05). Empty string, so
# the byte is dropped and the rest of the value keeps its spacing - a visible
# placeholder would corrupt the data it was trying to flag, and the affected
# cells are named to the user instead.
#
# Which bytes those are is openpyxl's ILLEGAL_CHARACTERS_RE, imported in app.py
# rather than restated here so the two cannot disagree. It leaves TAB, LF and CR
# alone, which is correct: Excel permits them and they carry meaning.
ILLEGAL_CHAR_REPLACEMENT = ''

# How many affected cells to name when reporting sanitised control characters.
SANITISED_REPORT_LIMIT = 10

# Leading characters that make a spreadsheet read a text cell as a formula
# (CSV/formula injection, CWE-1236). A value beginning with one of these - or a
# leading TAB/CR a spreadsheet trims first - is prefixed with an apostrophe so
# Excel keeps it as text rather than executing it. Warehouse rows are written by
# other users, so a cell like =HYPERLINK(...) would otherwise run on whoever
# opens the workbook. openpyxl only turns a leading '=' into a live formula, but
# a CSV consumer evaluates + - @ too; all are neutralised, matching the
# dremio_excel skill so the two exporters behave identically.
FORMULA_TRIGGER_CHARS = ('=', '+', '-', '@', '\t', '\r')

# The text marker prepended to a formula-like value. Excel hides it and stores
# the cell as text; other tools see a literal leading apostrophe.
FORMULA_PREFIX = "'"

# How often the Tk thread checks for work handed over by a worker thread.
# Workers never call into Tcl themselves - see DremioExporter._ui - so this
# interval is the delay between a worker posting an update and the UI showing
# it. Short enough to look immediate, long enough to cost nothing while idle.
UI_QUEUE_POLL_MS = 50


# =============================================================================
# QUERY SETTINGS
# =============================================================================

MAX_QUERY_HISTORY = 20

# How long a history dropdown label may be, and what marks the elided middle.
#
# The label used to be query[:50] + '...', which collides whenever two queries
# share a 50-character prefix (F-27). That is not a rare case - it is the normal
# one for this app: a wide SELECT list is the whole point, and the clause that
# distinguishes two otherwise-identical queries sits past character 50. Two
# entries then render byte-identically and the user has no way to pick the right
# one except trial and error.
#
# So labels elide the MIDDLE and keep both ends, which is what makes the
# trailing WHERE or LIMIT visible.
HISTORY_LABEL_LENGTH = 60
HISTORY_LABEL_ELLIPSIS = ' ... '

# Encodings tried, in order, when opening a .sql file that carries NO byte-order
# mark (F-18). The old code passed no encoding at all, so it got the platform
# default and raised UnicodeDecodeError on anything else - and .sql files arrive
# from SSMS, Notepad and older tools, which produce UTF-16 and cp1252 routinely.
#
#   utf-8   what almost everything writes now, and what this app writes
#   cp1252  the Windows Western default, for older files with no BOM
#
# A BOM, where present, is decisive and is handled separately - see
# DremioExporter._read_query_file. In particular UTF-16 is NOT in this list:
# without a BOM, decoding as UTF-16 assumes little-endian and turns any
# even-length byte sequence into plausible-looking nonsense instead of failing,
# so putting it here made every cp1252 file decode as garbage rather than raise.
#
# There is deliberately no latin-1 either. It decodes every byte sequence
# without complaint, so it would guarantee "success" by quietly turning an
# unreadable file into mojibake - and mojibake in a SQL statement is worse than
# a refusal, because it runs. cp1252 is the practical limit of that argument: it
# rejects only the five bytes it leaves undefined, so it catches a truly binary
# file but cannot detect every one. That is a known limit, not an oversight.
QUERY_FILE_ENCODINGS = ('utf-8', 'cp1252')

# What text files this app writes are encoded as. config.py already used this
# explicitly; app.py passed nothing and inherited the platform default, so the
# two disagreed about their own files (F-18).
TEXT_FILE_ENCODING = 'utf-8'

DEFAULT_QUERY = """-- Enter your SQL query here
SELECT * 
FROM your_space.your_folder.your_table
LIMIT 100"""


# =============================================================================
# CONNECTION SETTINGS
# =============================================================================

ROUTING_TAG = b"lid-toolbox-default-tag"

# SSL Certificate name to look for in Windows keychain
SSL_CERT_NAME = 'RWE Server Auth Issuing CA'


# =============================================================================
# FILE NAMES
# =============================================================================

CONFIG_FILENAME = 'config.json'
HISTORY_FILENAME = 'query_history.json'
CREDENTIALS_FILENAME = '.credentials'
SAVED_QUERIES_FOLDER = 'saved_queries'

# Asset files are auto-detected by extension in the assets/ folder
# Logo: First .png, .jpg, .jpeg, .gif, or .bmp file found
#       (prioritizes files with 'logo' in the name)
# Icon: First .ico file found
#       (prioritizes files with 'icon' in the name)
ASSETS_FOLDER = 'assets'

LOGO_SIZE = (45, 45)  # Width, Height in pixels for header logo
