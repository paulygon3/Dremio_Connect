"""
================================================================================
Dremio to Excel Exporter
================================================================================
A professional GUI application for querying Dremio via Arrow Flight
and exporting results to Excel.

Usage:
    python main.py

Or run as module:
    python -m dremio_exporter

Author: Paul - RWE Transmission Team
Version: 3.0.0
================================================================================
"""

import tkinter as tk
from app import DremioExporter


def main():
    """Application entry point."""
    # Create root window
    root = tk.Tk()
    
    # Create application
    app = DremioExporter(root)
    
    # Run main loop
    root.mainloop()


if __name__ == '__main__':
    main()
