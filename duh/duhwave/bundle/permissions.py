"""Bundle permissions — ADR-032 §B.

Declarative envelope of what a swarm is allowed to do: filesystem
reads/writes, network hostnames, D.U.H. tool names. Loaded from
``permissions.toml`` at the bundle root and enforced by the kernel
permission gate (ADR-005).

The ``diff`` method renders a human-readable changelog used at install
time so users can see what a bundle upgrade adds or removes before
approving.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(slots=True)
class BundlePermissions:
    """Declared FS/network/tool permissions for one bundle."""

    filesystem: dict[str, list[str]] = field(default_factory=dict)
    network: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    @classmethod
    def from_toml(cls, path: Path | str) -> BundlePermissions:
        """Read a ``permissions.toml`` file. Missing file = empty envelope."""
        p = Path(path)
        try:
            with p.open("rb") as f:
                raw = tomllib.load(f)
        except FileNotFoundError as e:
            raise PermissionsError(f"permissions.toml not found: {p}") from e

        fs_raw = raw.get("filesystem", {})
        if not isinstance(fs_raw, dict):
            raise PermissionsError("[filesystem] must be a table")
        filesystem: dict[str, list[str]] = {}
        for key, val in fs_raw.items():
            if not isinstance(val, list):
                raise PermissionsError(f"filesystem.{key} must be a list of patterns")
            filesystem[str(key)] = [str(x) for x in val]

        net_raw = raw.get("network", {})
        # Accept either [network] table with `allow` list, or a bare list.
        if isinstance(net_raw, dict):
            network = [str(x) for x in net_raw.get("allow", [])]
        elif isinstance(net_raw, list):
            network = [str(x) for x in net_raw]
        else:
            raise PermissionsError("[network] must be a list or table with 'allow'")

        tools_raw = raw.get("tools", {})
        if isinstance(tools_raw, dict):
            tools = [str(x) for x in tools_raw.get("require", [])]
        elif isinstance(tools_raw, list):
            tools = [str(x) for x in tools_raw]
        else:
            raise PermissionsError("[tools] must be a list or table with 'require'")

        return cls(filesystem=filesystem, network=network, tools=tools)

    def diff(self, other: BundlePermissions) -> str:
        """Render a human-readable diff vs. ``other`` (the *previously*-installed version).

        Lines beginning with ``+`` are added by *self* relative to *other*;
        lines with ``-`` are removed. Empty result = identical envelopes.
        """
        out: list[str] = []

        # Filesystem: keys may differ; flatten to (key, pattern) pairs.
        self_fs = {(k, p) for k, ps in self.filesystem.items() for p in ps}
        other_fs = {(k, p) for k, ps in other.filesystem.items() for p in ps}
        for key, pat in sorted(self_fs - other_fs):
            out.append(f"+ filesystem.{key}: {pat}")
        for key, pat in sorted(other_fs - self_fs):
            out.append(f"- filesystem.{key}: {pat}")

        self_net = set(self.network)
        other_net = set(other.network)
        for host in sorted(self_net - other_net):
            out.append(f"+ network: {host}")
        for host in sorted(other_net - self_net):
            out.append(f"- network: {host}")

        self_tools = set(self.tools)
        other_tools = set(other.tools)
        for t in sorted(self_tools - other_tools):
            out.append(f"+ tool: {t}")
        for t in sorted(other_tools - self_tools):
            out.append(f"- tool: {t}")

        return "\n".join(out)

    def is_empty(self) -> bool:
        return not self.filesystem and not self.network and not self.tools


class PermissionsError(ValueError):
    """``permissions.toml`` failed to parse."""
