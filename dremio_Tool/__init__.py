"""
Dremio to Excel Exporter
========================
A professional GUI application for querying Dremio via Arrow Flight
and exporting results to Excel.

Usage:
    from dremio_exporter import DremioExporter
    
    root = tk.Tk()
    app = DremioExporter(root)
    root.mainloop()
"""

from .app import DremioExporter
from .config import ConfigManager
from .connection import DremioConnection

__version__ = '3.0.0'
__author__ = 'Paul - RWE Transmission Team'

__all__ = ['DremioExporter', 'ConfigManager', 'DremioConnection']
