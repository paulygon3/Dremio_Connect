# Dremio to Excel Exporter

A professional GUI application for querying Dremio via Apache Arrow Flight and exporting results to Excel.

![Version](https://img.shields.io/badge/version-3.0.0-blue)
![Python](https://img.shields.io/badge/python-3.8+-green)

## Features

- **Arrow Flight Protocol** - High-performance data transfer from Dremio
- **Single-Page Interface** - All controls visible at once, no tab switching
- **Persistent Settings** - Credentials and preferences saved between sessions
- **Token Storage** - Uses the OS credential store where one is available, and a
  base64 file where it is not. Base64 is obfuscation, not encryption - see
  [Security](#security) before relying on it
- **Query History** - Quick access to recent queries
- **Excel Formatting** - Auto-fit columns, freeze headers, table styles
- **Custom Branding** - Use your own logo and icon

## Project Structure

```
dremio_exporter/
├── main.py              # Entry point - run this
├── app.py               # Main application UI
├── config.py            # Settings & persistence manager
├── connection.py        # Dremio connection & auth
├── constants.py         # Colors, defaults, app metadata
├── utils.py             # Helper functions
├── requirements.txt     # Python dependencies (pinned - see Development)
├── README.md            # This file
├── tests/               # One reproduction script per audit finding
│   ├── run_all.py       # Runs them all and prints a summary
│   ├── harness.py       # Shared helpers (Tk apps, dialog capture, temp homes)
│   └── flightserver.py  # Local Arrow Flight server, so tests need no Dremio
└── assets/
    ├── logo.png         # Header logo (45x45 recommended)
    └── icon.ico         # Window icon
```

## Installation

### 1. Clone/Download the project

```bash
cd C:\Users\YourName\Documents
git clone <repository> dremio_exporter
# OR download and extract the ZIP
```

### 2. Install dependencies

```bash
cd dremio_exporter
pip install -r requirements.txt
```

### 3. Add your images (optional)

Place your custom images in the `assets/` folder. The app **auto-detects** images by file extension:

| Type | Extensions | Priority Names |
|------|-----------|----------------|
| **Logo** | `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp` | Files with "logo", "brand", "header" in name |
| **Icon** | `.ico` | Files with "icon", "app", "favicon" in name |

**Examples that will work:**
```
assets/
├── my_company_logo.png     ✓ Auto-detected as logo
├── app_icon.ico            ✓ Auto-detected as icon
```

```
assets/
├── RWE_logo_2024.png       ✓ Auto-detected (has "logo" in name)
├── window.ico              ✓ Auto-detected (only .ico file)
```

**Image recommendations:**
- Logo: 45×45 pixels or larger (will be resized)
- Icon: 32×32 or 64×64 pixels

### 4. Run the application

```bash
python main.py
```

## Configuration

Settings are automatically saved to:

**Windows:** `%APPDATA%\DremioExporter\`
**Linux/Mac:** `~/.dremioexporter/`

### Files created:
- `config.json` - Connection settings, UI preferences
- `query_history.json` - Recent queries (last 20)
- `.credentials` - Base64-encoded token storage (fallback; see Security below)
- `saved_queries/` - Your saved .sql files

## Security

### Token Storage

The application uses a tiered approach for storing your PAT token:

1. **OS credential store** (recommended)
   - Requires the `keyring` package, which is in `requirements.txt`
   - On Windows this is Credential Manager, and tokens are encrypted by Windows
   - ⚠️ **Installing `keyring` is not the same as having a working backend.**
     On many Linux systems it resolves to `keyring.backends.fail.Keyring`, which
     stores nothing - so the base64 fallback below is the live path there, not a
     rare branch. The app does not announce which one it used.

2. **Fallback: Base64 Encoding**
   - Used if keyring is unavailable
   - ⚠️ **This is encoding, not encryption.** Base64 is trivially reversible.
     Anyone who can read `.credentials` can recover your PAT in full.
   - The file is created `0o600` and the containing directory `0o700`, so on a
     correctly configured system only your account can read it. That limits who
     can reach the token; it does not protect the token itself.
   - Treat a machine where others have root, or where the home directory is on
     shared or synced storage, as a machine where your PAT is exposed. Use the
     OS credential store (option 1) there.

### Best Practices

- Use Personal Access Tokens (PAT), not passwords
- PAT tokens expire after 180 days - regenerate as needed
- Uncheck "Remember token" on shared computers. With it unchecked the token is
  not written to disk, and the app also clears it from the form once it has
  connected - so it is not left sitting in a window for the rest of the day.
  You will need to re-enter it to reconnect, which is the point.

## Usage Guide

### 1. Connect to Dremio

1. Enter your Dremio hostname. A pasted URL is fine - any scheme, trailing path
   or embedded port is stripped, and an IPv6 literal is accepted
2. Enter port (default: 32010)
3. Enter your username
4. Enter your PAT token (not password!)
5. Click **Connect**

### 2. Write Your Query

- Enter SQL in the query editor
- Use the **Recent** dropdown to load past queries
- Click **Load** to open a .sql file
- Click **Save** to save your query

### 3. Export to Excel

1. Set your output folder using **...**
2. Customize the filename pattern
3. Click **Execute Query and Export to Excel**

#### What the export will and will not do

Excel has hard limits, and results from a real warehouse hit them. Rather than
failing part-way through the write with a message from the spreadsheet library,
the export checks first and tells you what it found:

| Situation | What happens |
|---|---|
| More than 1,048,575 rows | Refused before anything is written, naming the limit. Add a `LIMIT` or narrow the query - a worksheet cannot hold more, and the header uses the first row |
| A cell over 32,767 characters | Excel's per-cell limit, so the cell is shortened - but the **full** values are written to `<name>.truncated.txt` beside the workbook, and the affected cells are named in a dialog and the log |
| Control characters in the data | Removed, because Excel cannot store them, and the affected cells are named. Tabs and newlines are kept. Common in mainframe extracts and fixed-width files |
| The target file already exists | You are asked before it is replaced. Answering No leaves the original alone |
| The export fails for any reason | No half-written `.xlsx` is left behind |

Nothing here happens silently: if the workbook differs from the query result,
the log and a dialog say so before the success message.

The worksheet is named `Data`. To change it, set `output.sheet_name` in
`config.json` - the name is validated against Excel's rules, and an unusable one
is reported and ignored rather than producing a file Excel refuses to open.

## Customization

### Colors

Edit `constants.py` to change the color scheme:

```python
COLORS = {
    'header_bg': '#0066B3',      # Change header color
    'button_success': '#28A745',  # Change button colors
    # ...
}
```

### Default Settings

Edit `constants.py` to change defaults:

```python
DEFAULT_CONFIG = {
    'connection': {
        'port': '32010',         # Default port
        'use_tls': True,         # TLS enabled by default
    },
    # ...
}
```

## Troubleshooting

### "Invalid username or password"

- Use your **PAT Token**, not your Windows password
- Get PAT from: Dremio Web UI → Account Settings → Personal Access Tokens
- Check token hasn't expired (180 days)

### "Connection refused"

- Verify you're on the company VPN
- Check hostname and port are correct

### "SSL certificate error"

- Try unchecking "Use TLS/SSL" option
- Or ensure the RWE certificate is in your Windows keychain

### Logo/Icon not showing

- Ensure images are in `assets/` folder next to `main.py`
- Check file extensions: `.png`/`.jpg` for logo, `.ico` for icon
- Install Pillow for logo support: `pip install Pillow`
- Check console output for messages like:
  ```
  INFO: Loading logo from: assets/my_logo.png
  INFO: Loading icon from: assets/app_icon.ico
  ```
- If no images found, you'll see:
  ```
  INFO: No logo image found in assets/ folder
  INFO: No icon file found in assets/ folder
  ```

## Development

### Running tests

`tests/` holds one reproduction script per audit finding, not unit tests. Each
drives the real application code and prints a verdict - CONFIRMED (the defect is
present), NOT REPRODUCIBLE (it is not), or STILL BLOCKED (it could not be
measured). Run the lot with:

```bash
python tests/run_all.py            # every script, with a summary
python tests/run_all.py --list     # what exists, and what needs a display
python tests/run_all.py --only F-13,F-14
python tests/run_all.py -v         # full output from each script
```

Most need a display. `run_all.py` wraps those in `xvfb-run -a` automatically
when `$DISPLAY` is unset, so no wrapper is needed at the top level. Some stand
up a local Arrow Flight server (`tests/flightserver.py`) so the real connection
code can be exercised without a Dremio instance. A full run takes about seven
minutes, most of it one script that measures dialog-delivery rates over repeated
Tk sessions.

The findings themselves are documented in `docs/architecture/AUDIT.md`.

**When changing a dependency**, re-run the suite and check the summary is
unchanged. `requirements.txt` is pinned exactly so these verdicts have a
recorded runtime behind them; `tests/repro_f32_unpinned_requirements.py` fails
if the installed versions have drifted from the pins.

### Building executable

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=assets/icon.ico main.py
```

## Changelog

### v3.0.0
- Modular code structure
- Single-page layout
- Persistent settings
- Query history
- Custom branding support

### v2.0.0
- Added export path selection
- Added log export
- Improved UI

### v1.0.0
- Initial release

## License

Internal use only - RWE Transmission Team

## Author

Paul - RWE Transmission Team
