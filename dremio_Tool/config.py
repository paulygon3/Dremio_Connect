"""
================================================================================
config.py - Configuration Manager
================================================================================
Handles persistent storage of settings, credentials, and query history.

Storage location:
    Windows: %APPDATA%/DremioExporter/
    Linux/Mac: ~/.dremioexporter/

Files:
    - config.json: Connection settings, UI preferences
    - query_history.json: Recent queries (last 20)
    - .credentials: Encrypted token storage (fallback)
    - saved_queries/: User-saved query files

Security:
    - PAT tokens stored via Windows Credential Manager (keyring) when available
    - Falls back to base64-encoded storage if keyring unavailable
================================================================================
"""

import os
import json
import base64
from pathlib import Path
from datetime import datetime
from copy import deepcopy

from constants import (
    APP_NAME, 
    DEFAULT_CONFIG, 
    MAX_QUERY_HISTORY,
    HISTORY_LABEL_LENGTH,
    HISTORY_LABEL_ELLIPSIS,
    CONFIG_VERSION,
    LEGACY_SHEET_NAME,
    DEFAULT_SHEET_NAME,
    CONFIG_FILENAME,
    HISTORY_FILENAME,
    CREDENTIALS_FILENAME,
    SAVED_QUERIES_FOLDER
)

# Try to import keyring for secure credential storage
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False


def build_query_label(query, max_length=HISTORY_LABEL_LENGTH):
    """
    A one-line, bounded label for a query, elided in the middle.

    The old label was `query[:50] + '...'`, which is ambiguous exactly where
    this app is used most (F-27). Wide SELECT lists are its purpose, so two
    queries routinely share far more than 50 leading characters and differ only
    in a trailing WHERE or LIMIT - the part a prefix throws away. Keeping both
    ends puts the distinguishing clause back on screen.

    Whitespace is collapsed first: queries are usually typed across several
    indented lines, and a raw prefix of one is mostly leading spaces.

    Args:
        query: the full query text
        max_length: hard bound on the returned string

    Returns:
        str: never longer than max_length
    """
    collapsed = ' '.join(str(query).split())

    if max_length <= 0:
        return ''
    if len(collapsed) <= max_length:
        return collapsed

    # No room for both ends and a marker: fall back to a plain head, still
    # inside the bound.
    if max_length <= len(HISTORY_LABEL_ELLIPSIS):
        return collapsed[:max_length]

    keep = max_length - len(HISTORY_LABEL_ELLIPSIS)
    head = (keep + 1) // 2
    tail = keep - head
    # `collapsed[-0:]` is the WHOLE string, not the empty one, so a zero-width
    # tail would return more than max_length - the same negative/zero slice trap
    # as F-26 itself, which is why this is spelled out rather than sliced.
    ending = collapsed[-tail:] if tail else ''
    return collapsed[:head] + HISTORY_LABEL_ELLIPSIS + ending


def _marked_label(query, marker):
    """
    A label carrying a leading disambiguating marker, still inside the bound.

    The query is re-elided against the space the marker leaves, so the whole
    thing fits the dropdown rather than overflowing it. A marker that is itself
    longer than the bound would leave nothing for the query, so the marker is
    trimmed rather than allowed to push the label past the limit.
    """
    prefix = f"[{marker}] " if marker else ""
    if len(prefix) >= HISTORY_LABEL_LENGTH:
        return prefix[:HISTORY_LABEL_LENGTH]
    return prefix + build_query_label(
        query, HISTORY_LABEL_LENGTH - len(prefix)
    )


def _label_timestamp(raw):
    """
    A short, human time from a stored ISO timestamp, for disambiguation only.

    Returns '' when the value is missing or unparseable rather than raising:
    history.json is user-editable and this runs while building a dropdown, so a
    bad timestamp must cost a disambiguator, not the window.
    """
    if not raw:
        return ''
    try:
        return datetime.fromisoformat(raw).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return ''


class ConfigManager:
    """
    Manages persistent storage of application settings and credentials.
    
    Features:
        - Auto-saves settings to JSON files
        - Secure token storage via Windows Credential Manager
        - Query history with automatic deduplication
        - Saved query file management
    
    Usage:
        config = ConfigManager()
        
        # Access settings
        hostname = config.config['connection']['hostname']
        
        # Modify and save
        config.config['connection']['hostname'] = 'new.server.com'
        config.save_config()
        
        # Token management
        config.save_token('username', 'my_pat_token')
        token = config.get_token('username')
        
        # Query history
        config.add_to_history('SELECT * FROM table')
        recent = config.history  # List of recent queries
    """
    
    def __init__(self):
        """Initialize config manager and create app directory."""
        # Problems found while loading. The UI does not exist yet at this point
        # - ConfigManager is constructed as the first statement of
        # DremioExporter.__init__ - so they are collected here and drained into
        # the log panel once there is one.
        self.load_warnings = []
        # Installed by DremioExporter once the log panel exists, so problems
        # that happen later - a failed save, an unwritable directory - reach
        # the user instead of a console that is not there (F-20).
        self.on_warning = None

        self._setup_directories()
        self._load_config()
        self._load_history()
    
    def _setup_directories(self):
        """Create application data directories."""
        # Determine app data location based on OS
        if os.name == 'nt':  # Windows
            base_dir = os.environ.get('APPDATA', '')
            self.app_dir = Path(base_dir) / APP_NAME
        else:  # Linux/Mac
            self.app_dir = Path.home() / f'.{APP_NAME.lower()}'
        
        # Create directories, owner-only.
        # The mode passed to mkdir is masked by the umask, so chmod afterwards
        # forces it. This directory holds .credentials and config.json; the
        # latter carries the hostname the client connects to, so a writable
        # directory lets an attacker redirect the client AND supply the
        # credential it presents.
        self.app_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.app_dir, 0o700)
        except OSError as e:
            self.load_warnings.append(
                f"could not restrict permissions on {self.app_dir}: {e}"
            )

        # Define file paths
        self.config_file = self.app_dir / CONFIG_FILENAME
        self.history_file = self.app_dir / HISTORY_FILENAME
        self.credentials_file = self.app_dir / CREDENTIALS_FILENAME
        self.queries_dir = self.app_dir / SAVED_QUERIES_FOLDER
        
        # Create saved queries directory
        self.queries_dir.mkdir(exist_ok=True)
    
    def _load_config(self):
        """Load configuration from file or use defaults."""
        raw = self._load_json(self.config_file, self._get_default_config())

        coerced = self._coerce_config(raw)

        # Before the merge, deliberately. _merge_with_defaults back-fills every
        # missing key from DEFAULT_CONFIG, including the version stamp - so a
        # migration that ran afterwards would see the current version on a file
        # that has never been migrated, and never run.
        migrated = self._migrate_config(coerced)

        # Merge with defaults to handle new settings
        self.config = self._merge_with_defaults(coerced)

        if migrated:
            # Persist immediately, so the migration is recorded and cannot run a
            # second time against a value the user has since chosen on purpose.
            self.save_config()

    def _migrate_config(self, data):
        """
        Bring an older config.json up to the current shape, in place.

        Args:
            data: the parsed file, coerced to dict-of-dicts but NOT yet merged
                with defaults.

        Returns:
            bool: True if anything changed and the file should be rewritten.
        """
        version = data.get('meta', {}).get('config_version', 0)
        if version >= CONFIG_VERSION:
            return False

        changed = False

        if version < 1:
            # F-07. output.sheet_name existed from the beginning, was written to
            # every config.json, and was never read - the writer used the
            # literal 'Data'. Honouring the setting therefore renames the sheet
            # from 'Data' to 'Dremio Data' for everyone who has ever run the
            # app, breaking any formula, Power Query or macro that refers to it
            # by name.
            #
            # A stored 'Dremio Data' cannot be a deliberate choice, because
            # choosing it has never had any effect. So it is the old default,
            # and rewriting it to what the workbooks actually contain preserves
            # every existing export. Anyone who wants that name can set it now
            # and it will stick - the version stamp below means this never runs
            # against their choice.
            output = data.get('output')
            if isinstance(output, dict) and output.get('sheet_name') == LEGACY_SHEET_NAME:
                output['sheet_name'] = DEFAULT_SHEET_NAME
                changed = True

        # Stamped whether or not anything moved, so an already-correct old file
        # is not re-examined on every start.
        data.setdefault('meta', {})['config_version'] = CONFIG_VERSION
        return True

    def _load_history(self):
        """Load query history from file."""
        self.history = self._coerce_history(self._load_json(self.history_file, []))

    def _coerce_config(self, data):
        """
        Force loaded config into the dict-of-dicts shape every consumer assumes.

        _load_json catches only files that fail to *parse*. Nothing validated
        the shape of what parsed, and every consumer then assumed
        dict-of-dicts - so a file containing `[]`, `null`, `42`, or
        `{"connection": "oops"}` raised TypeError inside _merge_with_defaults.

        That happens during ConfigManager.__init__, which is the first statement
        of DremioExporter.__init__, before a single widget is created: the app
        died with a traceback and no window ever appeared. In a PyInstaller
        windowed build there is no console either, so the symptom was that
        double-clicking the icon did nothing, forever, and recovery required
        knowing to delete a JSON file from a hidden folder.

        Anything of the wrong shape is dropped and reported. Sections that are
        well-formed are kept, so one bad section does not cost the user the rest
        of their settings.
        """
        if not isinstance(data, dict):
            self.load_warnings.append(
                f"{self.config_file.name} contained "
                f"{type(data).__name__} where an object was expected - "
                f"settings were reset to defaults"
            )
            return {}

        cleaned = {}
        for section, values in data.items():
            if isinstance(values, dict):
                cleaned[section] = values
            else:
                self.load_warnings.append(
                    f"{self.config_file.name}: section '{section}' was "
                    f"{type(values).__name__}, not an object - that section was "
                    f"reset to defaults"
                )
        return cleaned

    def _coerce_history(self, data):
        """
        Force loaded history into the list-of-dicts shape consumers assume.

        get_history_labels does `h.get('label', '')` on every entry, so a bare
        list of query strings - the obvious v1 format, and the shape any older
        build or hand-edit would leave - raised AttributeError during
        _update_history_dropdown, still inside DremioExporter.__init__.

        Malformed entries are dropped rather than migrated: guessing at the
        meaning of an unknown shape risks putting the wrong text in front of
        someone about to run it as SQL.
        """
        if not isinstance(data, list):
            self.load_warnings.append(
                f"{self.history_file.name} contained "
                f"{type(data).__name__} where a list was expected - "
                f"query history was cleared"
            )
            return []

        cleaned = [entry for entry in data if isinstance(entry, dict)]
        discarded = len(data) - len(cleaned)
        if discarded:
            entries = ("1 history entry that was not an object" if discarded == 1
                       else f"{discarded} history entries that were not objects")
            self.load_warnings.append(
                f"{self.history_file.name}: discarded {entries}"
            )
        return cleaned
    
    def _get_default_config(self):
        """Get a fresh copy of default configuration."""
        config = deepcopy(DEFAULT_CONFIG)
        
        # Set default output directory to user's Documents
        if not config['output']['directory']:
            config['output']['directory'] = str(
                Path.home() / 'Documents' / 'Dremio_Exports'
            )
        
        return config
    
    def _merge_with_defaults(self, config):
        """
        Merge loaded config with defaults to ensure all keys exist.
        
        This handles the case where new settings are added in updates.
        """
        defaults = self._get_default_config()
        
        for section, values in defaults.items():
            if section not in config:
                config[section] = values
            elif isinstance(values, dict):
                for key, value in values.items():
                    if key not in config[section]:
                        config[section][key] = value
        
        return config
    
    def _load_json(self, filepath, default):
        """
        Load JSON file or return default value.
        
        Args:
            filepath: Path to JSON file
            default: Default value if file doesn't exist or is invalid
        
        Returns:
            Loaded data or default value
        """
        try:
            if filepath.exists():
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            self.load_warnings.append(
                f"{filepath.name} could not be read ({e}) - defaults were used"
            )

        return deepcopy(default)
    
    def _save_json(self, filepath, data, private=False):
        """
        Save data to a JSON file, atomically.

        open(path, 'w') truncates the moment it is called and only then starts
        writing, so any interruption in between - force-quit, power loss, full
        disk - leaves an empty or half-written file. That mattered more than it
        sounds: _load_json treats an unparseable file as "use defaults", so the
        app reopened looking factory-fresh, having silently lost the hostname,
        username, output folder, filename pattern, window size and last query
        (F-22). And _save_json is called on every Execute and again from
        _on_close, immediately before the window is destroyed.

        Writing to a sibling temp file and renaming it over the target makes
        the replacement atomic: a reader sees either the whole old file or the
        whole new one, never a torn one. The fsync before the rename is what
        makes that true after a power loss rather than only after a crash.

        Args:
            filepath: Path to JSON file
            data: Data to save
            private: create the temp file 0o600, for anything holding secrets.
                The mode has to be right on the temp file, since that is the
                inode the rename installs.

        Returns:
            bool: True if the file was replaced.
        """
        # Same directory, so the rename stays on one filesystem - os.replace is
        # only atomic within a filesystem.
        temp_path = filepath.with_name(filepath.name + '.tmp')
        try:
            if private:
                handle = self._open_private(temp_path)
            else:
                handle = open(temp_path, 'w', encoding='utf-8')

            with handle as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, filepath)
            return True
        except (IOError, OSError) as e:
            self._warn(f"could not save {filepath.name}: {e}")
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _warn(self, message):
        """
        Report a persistence problem on a channel the user can actually see.

        print() was the only error channel in this whole layer, and this app is
        built as a windowed PyInstaller executable - there is no console for
        those lines to appear in, so every save failure was silent (F-20).

        Warnings raised before the UI exists queue in load_warnings, which
        DremioExporter drains into the log panel once it has one. Afterwards
        they go straight there through the callback it installs.
        """
        if self.on_warning is not None:
            try:
                self.on_warning(message)
                return
            except Exception:
                # A broken reporter must not take the save path down with it.
                pass
        self.load_warnings.append(message)
    
    # =========================================================================
    # PUBLIC METHODS - Configuration
    # =========================================================================
    
    def save_config(self):
        """
        Save current configuration to file.

        Returns:
            bool: False if the file could not be replaced. Callers that care
                can say so; _save_json has already reported it either way.
        """
        return self._save_json(self.config_file, self.config)
    
    def reset_config(self):
        """Reset configuration to defaults."""
        self.config = self._get_default_config()
        self.save_config()
    
    def get(self, section, key, default=None):
        """
        Get a configuration value.
        
        Args:
            section: Config section (e.g., 'connection', 'output')
            key: Setting key within section
            default: Default value if not found
        
        Returns:
            Configuration value
        """
        return self.config.get(section, {}).get(key, default)
    
    def set(self, section, key, value):
        """
        Set a configuration value.
        
        Args:
            section: Config section
            key: Setting key
            value: Value to set
        """
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
    
    # =========================================================================
    # PUBLIC METHODS - Query History
    # =========================================================================
    
    def save_history(self):
        """Save query history to file. Returns False if it could not be saved."""
        return self._save_json(self.history_file,
                               self.history[-MAX_QUERY_HISTORY:])
    
    def add_to_history(self, query):
        """
        Add a query to history.
        
        Automatically deduplicates and maintains max size.
        
        Args:
            query: SQL query string
        """
        query = query.strip()
        if not query:
            return
        
        # Remove existing entry if present (will re-add at top)
        self.history = [h for h in self.history if h.get('query') != query]
        
        # Create history entry. The stored label is written for anything that
        # reads history.json directly; get_history_labels rebuilds it from the
        # query rather than trusting it, so an existing file full of old-style
        # prefix labels is corrected on read rather than needing a migration.
        entry = {
            'query': query,
            'timestamp': datetime.now().isoformat(),
            'label': build_query_label(query)
        }
        
        # Add to beginning of list
        self.history.insert(0, entry)
        
        # Trim to max size
        self.history = self.history[:MAX_QUERY_HISTORY]
        
        # Auto-save
        self.save_history()
    
    def clear_history(self):
        """Clear all query history."""
        self.history = []
        self.save_history()
    
    def get_history_labels(self):
        """
        Labels for the history dropdown, guaranteed distinguishable.

        Two things happen here that did not before (F-27).

        The label is rebuilt from the query rather than read from the stored
        `label` key. Stored labels may be old-style prefixes written by an
        earlier version, and a user's existing history.json is exactly where
        this defect is already sitting - rebuilding fixes it on read instead of
        requiring a migration.

        Labels that still collide are disambiguated by the entry's timestamp.
        Middle-elision handles the ordinary case, where the distinguishing
        clause is at one end or the other, but two queries can differ only
        somewhere in the elided middle - and an ambiguous dropdown is the whole
        finding, so the last resort has to actually resolve it rather than
        narrow the odds.

        The `[:60]` this used to apply is gone. The stored label was already
        capped shorter than 60, so that bound could never fire - it looked like
        a length guarantee while enforcing nothing.

        Returns:
            list: one label per history entry, in history order
        """
        queries = [h.get('query', '') for h in self.history]
        labels = [build_query_label(q) for q in queries]

        seen = {}
        for label in labels:
            seen[label] = seen.get(label, 0) + 1

        distinguished = []
        used = set()
        for label, query, entry in zip(labels, queries, self.history):
            if seen[label] == 1:
                candidate = label
            else:
                # The marker goes at the FRONT, and the query is re-elided to
                # leave room for it. Appending would have been tidier to write
                # and useless to look at: the dropdown shows a fixed number of
                # characters, so a marker on the end sits exactly where it
                # cannot be seen - which would leave two entries still
                # indistinguishable on screen, i.e. the finding, with extra
                # steps.
                marker = _label_timestamp(entry.get('timestamp'))
                candidate = _marked_label(query, marker)

                # A timestamp alone is not enough, and assuming it was put the
                # collision straight back: two queries added in the same second
                # carry the same one - which is what happens when they are added
                # programmatically or pasted in quick succession. An entry may
                # also have no timestamp, or an unparseable one. This last step
                # guarantees what the others only make likely.
                ordinal = 2
                while candidate in used:
                    candidate = _marked_label(
                        query, f"{marker} #{ordinal}" if marker else f"#{ordinal}"
                    )
                    ordinal += 1

            used.add(candidate)
            distinguished.append(candidate)

        return distinguished
    
    def get_query_from_history(self, index):
        """
        Get full query text from history by index.
        
        Args:
            index: Index in history list
        
        Returns:
            str or None: Query text
        """
        if 0 <= index < len(self.history):
            return self.history[index].get('query', '')
        return None
    
    # =========================================================================
    # PUBLIC METHODS - Credential Storage
    # =========================================================================
    
    def get_token(self, username):
        """
        Retrieve stored token for a username.
        
        Tries Windows Credential Manager first, falls back to file storage.
        
        Args:
            username: Username to look up
        
        Returns:
            str or None: Stored token
        """
        if not username:
            return None
        
        # Try keyring first (secure)
        if KEYRING_AVAILABLE:
            try:
                token = keyring.get_password(APP_NAME, username)
                if token:
                    return token
            except Exception as e:
                self._warn(f"credential store unavailable ({e}); using the local fallback file")
        
        # Fallback to file storage
        return self._get_token_from_file(username)
    
    def save_token(self, username, token):
        """
        Store token securely for a username.
        
        Uses Windows Credential Manager if available, otherwise file storage.
        
        Args:
            username: Username
            token: PAT token to store
        """
        if not username or not token:
            return
        
        # Try keyring first (secure)
        if KEYRING_AVAILABLE:
            try:
                keyring.set_password(APP_NAME, username, token)
                return
            except Exception as e:
                self._warn(f"credential store unavailable ({e}); using the local fallback file")
        
        # Fallback to file storage
        self._save_token_to_file(username, token)
    
    def delete_token(self, username):
        """
        Delete stored token for a username.
        
        Args:
            username: Username
        """
        if not username:
            return
        
        # Try keyring
        if KEYRING_AVAILABLE:
            try:
                keyring.delete_password(APP_NAME, username)
            except Exception:
                pass
        
        # Also clean up file storage
        self._delete_token_from_file(username)
    
    def _get_token_from_file(self, username):
        """Get token from fallback file storage."""
        if not self.credentials_file.exists():
            return None
        
        try:
            with open(self.credentials_file, 'r') as f:
                data = json.load(f)
            
            encoded = data.get(username, '')
            if encoded:
                return base64.b64decode(encoded.encode()).decode()
        except Exception as e:
            self._warn(f"could not read the saved token: {e}")
        
        return None
    
    def _open_private(self, filepath):
        """
        Open a file for writing, readable and writable by the owner only.

        Uses os.open with an explicit 0o600 mode rather than open() followed by
        os.chmod: chmod after the fact leaves a window in which the file exists
        at the permissive default. The mode argument is masked by the process
        umask, so os.chmod is still applied afterwards to force 0o600 even where
        the inherited umask would have cleared those bits.

        A plain open() takes 0o666 masked by whatever umask the process happened
        to inherit, which on a standard Linux default (022) is 0o644 -
        world-readable - and on a corporate setup without user-private groups
        (002) is 0o664, group-writable. Group- or world-writable matters more
        than readable here: it lets an attacker replace the stored token with
        one of their own choosing.
        """
        fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            handle = os.fdopen(fd, 'w')
        except Exception:
            os.close(fd)
            raise
        os.chmod(filepath, 0o600)
        return handle

    def _save_token_to_file(self, username, token):
        """Save token to fallback file storage (base64 encoded)."""
        try:
            data = {}
            if self.credentials_file.exists():
                with open(self.credentials_file, 'r') as f:
                    data = json.load(f)

            # Base64 encode (NOT secure, just obfuscation!)
            data[username] = base64.b64encode(token.encode()).decode()

            # private=True so the temp file - the inode the rename installs -
            # is 0o600 from the moment it exists (F-28).
            self._save_json(self.credentials_file, data, private=True)
        except Exception as e:
            self._warn(f"could not save the token: {e}")
    
    def _delete_token_from_file(self, username):
        """Delete token from file storage."""
        if not self.credentials_file.exists():
            return
        
        try:
            with open(self.credentials_file, 'r') as f:
                data = json.load(f)
            
            if username in data:
                del data[username]
                self._save_json(self.credentials_file, data, private=True)
        except Exception as e:
            self._warn(f"could not remove the saved token: {e}")
    
    # =========================================================================
    # PUBLIC METHODS - Saved Queries
    # =========================================================================
    
    def get_saved_queries(self):
        """
        Get list of saved query files.
        
        Returns:
            list: List of Path objects
        """
        return sorted(self.queries_dir.glob('*.sql'))
    
    @staticmethod
    def clean_query_name(name):
        """
        The filename a saved query will get, or '' if the name cannot be used.

        The sanitiser keeps alphanumerics and `._- `, which is what stops a name
        escaping the queries directory. It was written when nothing called this,
        and wiring the subsystem up (F-31) is what makes its edges reachable:

          - a name of only illegal characters reduced to '', producing the
            hidden file '.sql'
          - '..' survived intact, producing '...sql'
          - leading and trailing spaces survived into the filename, so 'report '
            and 'report' were different queries that looked identical in a list

        So the result is stripped of dots and spaces at both ends, and an empty
        result is reported rather than written.
        """
        kept = "".join(c for c in str(name) if c.isalnum() or c in '._- ')
        return kept.strip(' .')

    def save_query_file(self, name, query):
        """
        Save a query to the library.

        Args:
            name: Query name (without .sql extension)
            query: SQL query text

        Returns:
            Path: Path to saved file

        Raises:
            ValueError: if the name has nothing usable in it. Raised rather than
                silently writing '.sql', because the caller has a user in front
                of it who can be asked for a better name.
            OSError: if the write fails. The caller reports it - this module
                stays UI-agnostic.
        """
        safe_name = self.clean_query_name(name)
        if not safe_name:
            raise ValueError(
                f"'{name}' cannot be used as a query name. Use letters, "
                f"digits, spaces, dots, dashes or underscores."
            )

        filepath = self.queries_dir / f"{safe_name}.sql"

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(query)

        return filepath
    
    def load_query_file(self, filepath):
        """
        Load a query from a file.
        
        Args:
            filepath: Path to query file
        
        Returns:
            str: Query text
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    
    def delete_query_file(self, filepath):
        """
        Delete a saved query file.
        
        Args:
            filepath: Path to query file
        """
        Path(filepath).unlink(missing_ok=True)
