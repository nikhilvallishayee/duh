"""Remote bridge -- exposes Engine sessions over WebSocket.

The bridge allows external UIs, IDEs, and tools to connect to a
running D.U.H. instance and interact with it in real-time.

    from duh.bridge.server import BridgeServer

    server = BridgeServer(host="localhost", port=8765, token="secret")
    await server.start()
"""
