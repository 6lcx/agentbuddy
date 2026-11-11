"""External-write test double with a durable call ledger in isolated test data."""
import json
import os
from pathlib import Path
from mcp.server.mcpserver import MCPServer

server = MCPServer('t21-confirmation')


@server.tool()
def record_message(text: str) -> str:
    with (Path(os.environ['DATA_DIR']) / 'external-calls.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'text': text}) + '\n')
    return 'Recorded: ' + text


if __name__ == '__main__': server.run()
