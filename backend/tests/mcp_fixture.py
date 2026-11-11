"""Real MCP transport fixture; never imported by the application."""

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("test-tools")


@server.tool()
def echo(text: str) -> str:
    return text


if __name__ == "__main__":
    server.run(
        transport="streamable-http", host="127.0.0.1", port=int(os.environ["MCP_TEST_PORT"])
    ) if os.environ.get("MCP_TEST_TRANSPORT") else server.run()
