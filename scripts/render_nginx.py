"""Render a same-origin static frontend + API/SSE nginx site."""

import argparse
from pathlib import Path

from agentbuddy.config import Settings


def render(root, app="/app", api="/api/v1", port=8080, backend_port=8000):
    Settings(api_prefix=app)
    Settings(api_prefix=api)
    if app == api or app.startswith(api + "/") or api.startswith(app + "/"):
        raise ValueError("Page and API prefixes must not overlap")
    if not 1 <= port <= 65535 or not 1 <= backend_port <= 65535:
        raise ValueError("Invalid port")
    root = str(Path(root).resolve()).replace("\\", "/")
    if any(c in root for c in '\n\r"'):
        raise ValueError("Invalid path")
    return f'''worker_processes 1;
pid nginx.pid;
error_log error.log;
events {{ worker_connections 256; }}
http {{
  include /etc/nginx/mime.types;
  access_log access.log;
  server {{
    listen 127.0.0.1:{port};
    root "{root}";
    client_max_body_size 80m;
    location = / {{ return 302 {app}/; }}
    location = {app} {{ return 302 {app}/; }}
    location {app}/ {{ try_files $uri $uri/ {app}/index.html; }}
    location {api}/ {{
      proxy_pass http://127.0.0.1:{backend_port};
      proxy_http_version 1.1;
      proxy_buffering off;
      proxy_read_timeout 3600s;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto $scheme;
    }}
  }}
}}
'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--app-prefix", default="/app")
    parser.add_argument("--api-prefix", default="/api/v1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--backend-port", type=int, default=8000)
    args = parser.parse_args()
    print(
        render(
            args.root, args.app_prefix, args.api_prefix, args.port, args.backend_port
        )
    )
