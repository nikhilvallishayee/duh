# ADR-042: Remote Bridge

**Status:** Accepted — partial (`BridgeServer` with WebSocket relay, bearer-token
auth, session registration, and prompt injection is implemented in
`duh/bridge/server.py`. Differences from this ADR on main today: default port is
**8765** (not 9120); the token must be supplied via config/CLI — not auto-generated
on start; rate limiting (10 msg/s) and `--remote-bridge-public` warning flag are
unimplemented. Per-message size cap is 1 MB via `websockets.serve(max_size=…)`;
explicit max-client cap (5) is not yet enforced.)
**Date**: 2026-04-08

## Context

D.U.H. is a terminal application — it can only be used from the machine where it's running. Users frequently want to:
- Monitor a long-running coding session from their phone
- Interact with duh from a web browser on another machine
- Connect a web-based IDE to a duh instance running on a dev server
- Share a session with a colleague for pair programming

There is no mechanism for remote access. Adding a thin relay layer would unlock all of these use cases without changing the core engine.

## Decision

Add an optional WebSocket server that relays engine events to remote clients:

### Architecture

```
[duh engine] → [RemoteBridge] → [WebSocket server] → [remote clients]
                                        ↑
                              [Token-based auth]
```

The bridge is an observer of the engine — it subscribes to engine events and forwards them. Client messages (user input, commands) are injected back into the engine's input queue.

### Bridge Implementation

```python
class RemoteBridge:
    def __init__(self, engine: Engine, host: str = "127.0.0.1", port: int = 9120):
        self.engine = engine
        self.host = host
        self.port = port
        self.auth_token: str | None = None
        self._clients: set[WebSocketConnection] = set()

    async def start(self) -> None:
        self.auth_token = secrets.token_urlsafe(32)
        print(f"Remote bridge: ws://{self.host}:{self.port}")
        print(f"Auth token: {self.auth_token}")
        # Start WebSocket server

    async def on_engine_event(self, event: EngineEvent) -> None:
        """Forward engine events to all connected clients."""
        payload = serialize_event(event)
        for client in self._clients:
            await client.send(payload)

    async def on_client_message(self, client, message: dict) -> None:
        """Inject client input into engine."""
        if message["type"] == "user_input":
            await self.engine.inject_input(message["text"])
        elif message["type"] == "command":
            await self.engine.execute_command(message["command"])
```

### Authentication

Token-based authentication — simple and sufficient for the initial version:

1. On bridge start, generate a random 32-byte token
2. Print the token to the terminal (visible to the local user)
3. Clients must send the token in their first WebSocket message
4. Connections without valid tokens are immediately closed

No OAuth, no user accounts, no session cookies. The token is a shared secret between the local user and their remote clients.

### Event Protocol

Events are JSON objects with a type discriminator:

```json
{"type": "assistant_message", "content": "...", "timestamp": 1234567890}
{"type": "tool_use", "tool": "Edit", "input": {...}, "timestamp": 1234567890}
{"type": "tool_result", "tool": "Edit", "output": "...", "timestamp": 1234567890}
{"type": "status", "state": "waiting_for_input", "timestamp": 1234567890}
```

### Security Constraints

- **Default bind**: `127.0.0.1` (localhost only). Binding to `0.0.0.0` requires explicit `--remote-bridge-public` flag with a warning.
- **No file transfer**: Remote clients cannot directly read/write files. All file operations go through the model.
- **Rate limiting**: Max 10 messages/second per client to prevent abuse.
- **Max clients**: 5 concurrent connections.

### Activation

The bridge is off by default. Enable via:
- CLI flag: `duh --remote-bridge`
- Config: `remote.enabled = true` in `duh.toml`
- Runtime: `/bridge start` command

## Consequences

### Positive
- Unlocks mobile monitoring, web access, and pair programming
- Observer pattern means zero changes to the core engine
- Token auth is simple and requires no infrastructure
- Localhost-default means no accidental exposure

### Negative
- WebSocket server adds a dependency (`websockets` library)
- No encryption by default on localhost — remote use requires TLS termination (nginx, etc.)
- Token auth is not suitable for production multi-user deployments

### Risks
- Accidental binding to `0.0.0.0` exposes the session to the network — mitigated by requiring explicit flag and printing a warning
- Injected input from remote clients could bypass approval gates — mitigated by routing all input through the same engine path that handles local input
- Future OAuth/TLS needs may require significant rework — mitigated by the simple bridge interface that can be swapped

## Implementation Notes

- `duh/bridge/protocol.py` — message dataclasses (`ConnectMessage`, `PromptMessage`,
  `EventMessage`, `DisconnectMessage`, `ErrorMessage`), `encode_message` /
  `decode_message`, and `validate_token`.
- `duh/bridge/session_relay.py` — `SessionRelay` mapping session IDs to websockets.
- `duh/bridge/server.py` — `BridgeServer(host="localhost", port=8765, token=..., engine_factory=...)`.
- CLI entry: `duh bridge serve` in `duh/cli/parser.py` + `duh/cli/main.py`.
- Optional dep: `websockets` (see `duh/_optional_deps.py`).
