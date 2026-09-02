#!/usr/bin/env python3
"""Seed the `agent_extract` pseudo-transformation into OpenNotebook.

Idempotent: checks GET /api/transformations for name == "agent_extract"
first and only creates it when missing. The transformation's title is passed
to the agent service as the insight_type, so it is user-tunable after
creation via the Transformations UI.

Usage:
    python3 scripts/seed_agent_transformation.py [--api-url http://localhost:5055] [--token <ON password>]
"""

import argparse
import os
import sys
import urllib.error
import urllib.request

DEFAULT_API_URL = os.environ.get("ON_BASE_URL", "http://localhost:5055")
TRANSFORMATION_NAME = "agent_extract"


def request(url: str, token: str | None, method: str = "GET", body: bytes | None = None) -> dict | list:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        import json

        return json.loads(response.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--token", default=os.environ.get("ON_API_TOKEN"))
    parser.add_argument(
        "--title",
        default="核心观点",
        help="insight_type passed to the agent service (default: 核心观点)",
    )
    args = parser.parse_args()

    base = args.api_url.rstrip("/")
    existing = request(f"{base}/api/transformations", args.token)
    for trans in existing:
        if trans.get("name") == TRANSFORMATION_NAME:
            print(f"OK: transformation '{TRANSFORMATION_NAME}' already exists (id={trans['id']})")
            return 0

    import json

    payload = json.dumps(
        {
            "name": TRANSFORMATION_NAME,
            "title": args.title,
            "description": (
                "伪转换规则：不在 OpenNotebook 内跑转换流水线，而是调用外部"
                " agent 服务（deepagents 编排提取）异步生成见解并写回。"
            ),
            "prompt": "（由外部 agent 服务执行，此处不使用）",
            "apply_default": False,
        }
    ).encode()

    created = request(
        f"{base}/api/transformations", args.token, method="POST", body=payload
    )
    print(f"Created transformation '{TRANSFORMATION_NAME}' (id={created['id']})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Cannot reach OpenNotebook API: {e}", file=sys.stderr)
        sys.exit(1)
