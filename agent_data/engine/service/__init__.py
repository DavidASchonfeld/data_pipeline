# The service layer: how outside callers (HTTP clients, MCP clients, the dashboard) reach the engine.
# Each front door is a thin wrapper around the same service functions, so behaviour never drifts between them.
