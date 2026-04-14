"""MCP server capability manifest loader (ADR-054, 7.6).

A manifest declares the network and filesystem capabilities that an MCP
stdio server legitimately needs.  The MCPExecutor reads the manifest before
connecting and uses it to derive the sandbox policy applied to the subprocess.

Manifest JSON format (all keys optional)::

    {
        "network_allowed": false,
        "writable_paths": ["/tmp/my-mcp-server"],
        "readable_paths": ["/home/user/data"]
    }

When no manifest file exists (the common case for third-party servers),
``DEFAULT_MCP_MANIFEST`` is used: no network access, no additional writable
or readable paths beyond the sandbox's built-in defaults (/tmp, ~/.duh).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["MCPManifest", "DEFAULT_MCP_MANIFEST", "load_mcp_manifest"]


@dataclass(frozen=True)
class MCPManifest:
    """Declared capabilities for an MCP stdio server.

    All fields default to the most restrictive safe values.  A server that
    needs network access or specific filesystem paths must declare them
    explicitly in its manifest file.

    Attributes:
        network_allowed: Whether the subprocess may make outbound connections.
        writable_paths: Additional paths the subprocess may write to.
            The sandbox always grants write access to /tmp and ~/.duh.
        readable_paths: Additional paths the subprocess needs read access to
            beyond global read (which the sandbox grants by default).
    """

    network_allowed: bool = False
    writable_paths: frozenset[Path] = field(default_factory=frozenset)
    readable_paths: frozenset[Path] = field(default_factory=frozenset)


#: Restrictive default: no network, no extra filesystem access.
DEFAULT_MCP_MANIFEST = MCPManifest()


def load_mcp_manifest(path: Path) -> MCPManifest:
    """Load an MCPManifest from a JSON file.

    Returns ``DEFAULT_MCP_MANIFEST`` if the file does not exist or cannot be
    parsed.  Errors are logged as warnings rather than raised so that a missing
    manifest does not prevent the server from starting (it will just be more
    restricted).

    Args:
        path: Absolute path to the manifest JSON file.

    Returns:
        An :class:`MCPManifest` populated from the JSON, or the default.
    """
    if not path.exists():
        return DEFAULT_MCP_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse MCP manifest %s: %s", path, exc)
        return DEFAULT_MCP_MANIFEST
    return MCPManifest(
        network_allowed=bool(data.get("network_allowed", False)),
        writable_paths=frozenset(Path(p) for p in data.get("writable_paths", [])),
        readable_paths=frozenset(Path(p) for p in data.get("readable_paths", [])),
    )
