"""
================================================================================
utils.py - Utility Functions
================================================================================
Helper functions used throughout the application.
================================================================================
"""

import ipaddress
import re
import sys
from pathlib import Path
from datetime import datetime

from constants import (
    LOGO_SIZE,
    EXCEL_MAX_SHEET_NAME, EXCEL_FORBIDDEN_SHEET_CHARS,
    EXCEL_RESERVED_SHEET_NAME,
)


# =============================================================================
# PATH UTILITIES
# =============================================================================

def get_script_directory():
    """
    Get the directory where the main script is located.
    
    Works both when running as a script and as a compiled executable.
    
    Returns:
        Path: Directory containing the application
    """
    if getattr(sys, 'frozen', False):
        # Running as compiled executable (PyInstaller, cx_Freeze, etc.)
        return Path(sys.executable).parent
    else:
        # Running as script
        return Path(__file__).parent


def get_asset_path(filename):
    """
    Get the full path to an asset file.
    
    Searches for the file in multiple locations:
    1. assets/ folder next to the script
    2. Same folder as the script
    3. Current working directory's assets/ folder
    4. Current working directory
    
    Args:
        filename: Name of the asset file (e.g., 'logo.png')
    
    Returns:
        Path or None: Full path to the file if found, None otherwise
    """
    script_dir = get_script_directory()
    
    # Locations to check (in order of priority)
    locations = [
        script_dir / 'assets' / filename,
        script_dir / filename,
        Path.cwd() / 'assets' / filename,
        Path.cwd() / filename,
    ]
    
    for path in locations:
        if path.exists():
            return path
    
    return None


def get_logo_path():
    """
    Auto-detect logo image in assets folder.
    
    Searches for PNG files in the assets folder.
    Prioritizes files with 'logo' in the name.
    
    Returns:
        Path or None: Path to logo image file
    """
    return _find_asset_by_extension(
        extensions=['.png', '.jpg', '.jpeg', '.gif', '.bmp'],
        preferred_names=['logo', 'brand', 'header', 'icon']
    )


def get_icon_path():
    """
    Auto-detect window icon in assets folder.
    
    Searches for ICO files in the assets folder.
    Prioritizes files with 'icon' in the name.
    
    Returns:
        Path or None: Path to icon file
    """
    return _find_asset_by_extension(
        extensions=['.ico'],
        preferred_names=['icon', 'app', 'logo', 'favicon']
    )


def _find_asset_by_extension(extensions, preferred_names=None):
    """
    Find an asset file by extension with smart prioritization.
    
    Args:
        extensions: List of file extensions to search for (e.g., ['.png', '.ico'])
        preferred_names: List of preferred name patterns (e.g., ['logo', 'icon'])
    
    Returns:
        Path or None: Best matching file path
    
    Search priority:
        1. Files matching preferred names in assets/ folder
        2. Any file with matching extension in assets/ folder
        3. Files in script directory
        4. Files in current working directory
    """
    script_dir = get_script_directory()
    
    # Directories to search (in order)
    search_dirs = [
        script_dir / 'assets',
        script_dir,
        Path.cwd() / 'assets',
        Path.cwd(),
    ]
    
    # Normalize extensions to lowercase with dot
    extensions = [ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in extensions]
    preferred_names = [name.lower() for name in (preferred_names or [])]
    
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        
        # Find all matching files
        matching_files = []
        for ext in extensions:
            matching_files.extend(search_dir.glob(f'*{ext}'))
        
        if not matching_files:
            continue
        
        # Sort by preference
        def get_priority(filepath):
            name = filepath.stem.lower() #pulls filename without extension
            # Check if any preferred name is in the filename
            for i, pref in enumerate(preferred_names):
                if pref in name:
                    return i  # Lower index = higher priority
            return len(preferred_names)  # No match = lowest priority
        
        # Sort by priority, then by name length (shorter = simpler = better)
        matching_files.sort(key=lambda f: (get_priority(f), len(f.stem)))
        
        # Return best match
        return matching_files[0]
    
    return None


def list_assets():
    """
    List all detected assets for debugging.
    
    Returns:
        dict: Dictionary with 'logo' and 'icon' paths (or None)
    """
    return {
        'logo': get_logo_path(),
        'icon': get_icon_path(),
    }


# =============================================================================
# IMAGE UTILITIES
# =============================================================================

def load_logo_image():
    """
    Load and resize the logo image for use in tkinter.
    
    Auto-detects PNG/JPG files in the assets folder.
    
    Returns:
        ImageTk.PhotoImage or None: The loaded image, or None if not available
    """
    try:
        from PIL import Image, ImageTk
    except ImportError:
        print("INFO: Pillow not installed - logo will use text fallback")
        print("      Install with: pip install Pillow")
        return None
    
    logo_path = get_logo_path()
    if not logo_path:
        print("INFO: No logo image found in assets/ folder")
        print("      Supported formats: .png, .jpg, .jpeg, .gif, .bmp")
        return None
    
    try:
        print(f"INFO: Loading logo from: {logo_path}")
        img = Image.open(logo_path)
        img = img.resize(LOGO_SIZE, Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"ERROR: Could not load logo: {e}")
        return None


def load_icon(root):
    """
    Set the window icon for a tkinter root window.
    
    Auto-detects ICO files in the assets folder.
    
    Args:
        root: tkinter Tk() root window
    
    Returns:
        bool: True if icon was loaded successfully
    """
    icon_path = get_icon_path()
    if not icon_path:
        print("INFO: No icon file found in assets/ folder")
        print("      Supported format: .ico")
        return False
    
    try:
        print(f"INFO: Loading icon from: {icon_path}")
        root.iconbitmap(str(icon_path))
        return True
    except Exception as e:
        print(f"ERROR: Could not load icon: {e}")
        return False


# =============================================================================
# STRING UTILITIES
# =============================================================================

def truncate_string(text, max_length=50, suffix='...'):
    """
    Truncate a string to a maximum length with a suffix.

    The length bound is the contract; the suffix is decoration. Where the two
    conflict, the bound wins - which is the fix for F-26.

    Previously the last line was `text[:max_length - len(suffix)] + suffix`
    unconditionally. When max_length was smaller than the suffix, that bound
    went negative and sliced from the END, so the function returned a string
    *longer* than the limit it had been asked to enforce:

        truncate_string('abcdefghij', max_length=2) -> 'abcdefghi...'  (12)
        truncate_string('abcdefghij', max_length=0) -> 'abcdefg...'    (10)

    Same shape as F-01: correct across the first range, silently wrong past a
    boundary, no error raised. It was latent - the function has no callers, and
    config.py reimplemented the logic inline rather than calling it - so this
    defuses a trap for whoever wires it up rather than repairing live damage.

    Args:
        text: The string to truncate
        max_length: Maximum length of the result, including the suffix
        suffix: String to append if truncated

    Returns:
        str: never longer than max_length
    """
    if max_length <= 0:
        return ''

    if len(text) <= max_length:
        return text

    # Not enough room for the marker and any text with it. Truncate plainly:
    # a suffix that pushes the result past the limit defeats the whole call.
    #
    # Strictly less-than, so max_length == len(suffix) keeps returning the bare
    # suffix as it always did - that case was already inside the bound, and the
    # audit verified it. Only the broken range changes.
    if max_length < len(suffix):
        return text[:max_length]

    return text[:max_length - len(suffix)] + suffix


def generate_timestamp_filename(pattern, timestamp=None):
    """
    Generate a filename from a pattern with timestamp substitution.
    
    Args:
        pattern: Filename pattern with {timestamp} placeholder
        timestamp: datetime object (defaults to now)
    
    Returns:
        str: Filename with timestamp substituted
    """
    if timestamp is None:
        timestamp = datetime.now()
    
    timestamp_str = timestamp.strftime('%Y%m%d_%H%M%S')
    return pattern.replace('{timestamp}', timestamp_str)


# =============================================================================
# VALIDATION UTILITIES
# =============================================================================

def validate_connection_params(hostname, port, username, token):
    """
    Validate connection parameters and return the canonical form of them.

    Returning the cleaned values is the substance of the fix for F-24, not a
    convenience. Validation used to check one thing and the connection then used
    another: the port was parsed with int() and the RAW STRING was handed on, so
    '  32010  ' validated as 32010 and then built the URI
    "grpc+tls://host:  32010  ". int() also accepts Unicode decimal digits and a
    leading sign, so '１２３４' and '+32010' passed too. Hostname had the same
    split: checked for non-emptiness, then cleaned separately by the caller -
    which is how 'https://' passed validation and produced "grpc+tls://:32010".

    Callers should use params['hostname'] and params['port'], not what they
    passed in. Then the value that was checked is the value that is used, by
    construction rather than by discipline.

    Args:
        hostname: Server hostname, possibly with scheme, path or port
        port: Server port
        username: Username
        token: PAT token

    Returns:
        tuple: (is_valid, error_message, params). params is None when invalid,
            otherwise {'hostname': str, 'port': str} - both canonical.
    """
    if not hostname or not hostname.strip():
        return False, "Hostname is required", None

    host = clean_hostname(hostname)
    host_error = _hostname_error(host)
    if host_error:
        return False, host_error, None

    if not port or not str(port).strip():
        return False, "Port is required", None

    port_text = str(port).strip()
    if not _PORT_RE.match(port_text):
        return (
            False,
            "Port must be a number between 1 and 65535.\n\n"
            "Use plain digits only - no sign, no spaces.",
            None,
        )

    port_num = int(port_text)
    if port_num < 1 or port_num > 65535:
        return False, "Port must be between 1 and 65535", None

    if not username or not username.strip():
        return False, "Username is required", None

    if not token or not token.strip():
        return False, "PAT Token is required", None

    return True, None, {'hostname': host, 'port': str(port_num)}


def validate_output_filename(pattern):
    """
    Validate the export filename pattern and normalise its extension.

    The Filename field is a free-text entry with no validation, which let three
    unrelated things through: an empty value (which resolves to the output
    directory itself and raises IsADirectoryError), a value with no extension
    (which Excel will not open), and a relative path that escapes the chosen
    output folder entirely.

    Note this validates the *pattern*, before {timestamp} substitution. A
    pattern that is safe stays safe afterwards, because the substituted value is
    a fixed-format timestamp containing no separators.

    Args:
        pattern: raw filename pattern from the UI

    Returns:
        tuple: (is_valid, error_message, normalised_pattern)
    """
    if pattern is None or not pattern.strip():
        return False, "Filename is required", None

    name = pattern.strip()

    if '/' in name or '\\' in name or name in ('.', '..'):
        return (
            False,
            "Filename must be a name, not a path.\n\n"
            "Use the Output Folder field to choose where the file goes.",
            None,
        )

    if name.startswith('.'):
        return False, "Filename must not start with a dot", None

    # Characters Windows rejects in a filename. Excluded even on POSIX, since
    # the exports are routinely opened on Windows.
    invalid = set('<>:"|?*') & set(name)
    if invalid:
        return (
            False,
            f"Filename contains characters that are not allowed: "
            f"{' '.join(sorted(invalid))}",
            None,
        )

    # Enforce an extension by appending rather than rejecting: openpyxl only
    # writes .xlsx, so this is never the wrong answer, and it does not block
    # someone who simply typed "report".
    if not name.lower().endswith('.xlsx'):
        name = f"{name}.xlsx"

    return True, None, name


# Any scheme, not just the two that were spelled out, and case-insensitively.
_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://')

# A DNS label: alphanumerics and inner hyphens. Deliberately does not allow
# underscores; Dremio hostnames are DNS names, and an underscore is a common
# typo for a hyphen that would otherwise fail much later with a DNS error.
_LABEL_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$')

# ASCII digits only. int() accepts Unicode decimal digits and a leading sign,
# so '１２３４' and '+32010' both become ints and neither survives a URI.
_PORT_RE = re.compile(r'^[0-9]{1,5}$')


def validate_sheet_name(name):
    """
    Check a worksheet name against Excel's rules, all of them.

    This exists because openpyxl enforces only some. Measured against 25.0.1:

        rejected  empty, and the six characters [ ] : * ? / \\
        ACCEPTED  names longer than 31 characters (a UserWarning to stderr,
                  which a windowed PyInstaller build does not have)
        ACCEPTED  a leading or trailing apostrophe
        ACCEPTED  'History', which Excel reserves

    So the three openpyxl waves through are the ones that produce a workbook
    Excel refuses to open or silently repairs - the worst outcome, because the
    export reports success. AUDIT.md's F-07 makes the same point from the other
    direction: the rules were unreachable only because nothing read the setting,
    and wiring it up without validation would open all six at once.

    Args:
        name: the configured sheet name

    Returns:
        tuple: (is_valid, error_message, cleaned). cleaned is the stripped name
            when valid, None otherwise.
    """
    if name is None:
        return False, "Sheet name is required", None

    cleaned = str(name).strip()

    if not cleaned:
        return False, "Sheet name is required - it cannot be blank", None

    if len(cleaned) > EXCEL_MAX_SHEET_NAME:
        return (
            False,
            f"Sheet name is {len(cleaned)} characters; Excel allows at most "
            f"{EXCEL_MAX_SHEET_NAME}",
            None,
        )

    found = [c for c in EXCEL_FORBIDDEN_SHEET_CHARS if c in cleaned]
    if found:
        return (
            False,
            f"Sheet name cannot contain {' '.join(found)} - Excel forbids "
            f"{' '.join(EXCEL_FORBIDDEN_SHEET_CHARS)}",
            None,
        )

    if cleaned.startswith("'") or cleaned.endswith("'"):
        return (
            False,
            "Sheet name cannot begin or end with an apostrophe",
            None,
        )

    if cleaned.lower() == EXCEL_RESERVED_SHEET_NAME.lower():
        return (
            False,
            f"'{cleaned}' is reserved by Excel and cannot be used as a sheet "
            f"name",
            None,
        )

    return True, None, cleaned


def clean_hostname(hostname):
    """
    Clean and normalize a hostname.

    Removes protocol prefixes, paths, and port numbers, and returns the host in
    the form a URI wants it - which for an IPv6 literal means bracketed.

    The previous implementation was three separate defects (F-23):

      - `.replace('https://', '')` is case-sensitive, so `HTTPS://host` kept its
        scheme, and the subsequent split on ':' then returned the literal string
        'HTTPS'. Pasting a URL from a browser that capitalises the scheme
        produced a DNS error naming a host the user never typed.
      - the unconditional `split(':')[0]` destroyed every IPv6 literal: '::1'
        became '' and '[::1]:32010' became '['.
      - `rstrip('/')` removes trailing slashes only, so 'host/api/v3' survived
        into the Flight URI intact.

    Args:
        hostname: Raw hostname string

    Returns:
        str: Cleaned hostname, bracketed if it is an IPv6 literal
    """
    if not hostname:
        return ''

    host = hostname.strip()
    host = _SCHEME_RE.sub('', host)

    # Drop any path, query or fragment. rstrip('/') only ever caught the
    # trailing case.
    for separator in ('/', '?', '#'):
        host = host.split(separator, 1)[0]

    host = host.strip()
    if not host:
        return ''

    # Bracketed IPv6, with or without a port: [::1] / [::1]:32010
    if host.startswith('['):
        closing = host.find(']')
        if closing != -1:
            return host[:closing + 1]
        return host

    # A bare IPv6 literal has more than one colon and must be bracketed to sit
    # in a URI at all. Checking for a valid address rather than just counting
    # colons keeps 'a:b:c' - which is nothing - from being dressed up as one.
    if host.count(':') > 1:
        try:
            ipaddress.IPv6Address(host)
            return f"[{host}]"
        except ValueError:
            return host

    # host:port - strip the port only when what follows really is one.
    if ':' in host:
        head, _, tail = host.rpartition(':')
        if _PORT_RE.match(tail):
            return head

    return host


def _hostname_error(host):
    """
    Why this cleaned hostname cannot be connected to, or None if it can.

    Shape only. Whether the name resolves and answers is the connection's
    business - the point here is to reject what provably cannot work before the
    user waits on a socket for it.
    """
    if not host:
        return (
            "Hostname is required.\n\n"
            "If you pasted a URL, it contained no host - check for a stray "
            "'https://' with nothing after it."
        )

    if host.startswith('['):
        if not host.endswith(']'):
            return f"Unbalanced brackets in the IPv6 address: {host}"
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError:
            return f"Not a valid IPv6 address: {host[1:-1]}"
        return None

    if len(host) > 253:
        return "Hostname is longer than 253 characters"

    labels = host.split('.')
    for label in labels:
        if not label:
            return (
                f"'{host}' has an empty part - check for a doubled or trailing "
                f"dot"
            )
        if not _LABEL_RE.match(label):
            return (
                f"'{host}' is not a valid hostname.\n\n"
                f"'{label}' contains characters a hostname cannot: use letters, "
                f"digits, hyphens and dots, with no spaces, scheme or path."
            )

    return None
