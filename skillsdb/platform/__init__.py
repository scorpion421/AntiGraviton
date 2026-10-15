"""
Platform utilities: Windows UTF-8 manifests and native MCP JSON-RPC server.
"""

from .windows_utf8 import check_windows_utf8, fix_utf8
from .mcp_server import run_mcp_server

__all__ = [
    "check_windows_utf8",
    "fix_utf8",
    "run_mcp_server",
]
