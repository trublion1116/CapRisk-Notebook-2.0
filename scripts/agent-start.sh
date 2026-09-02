#!/bin/bash
# 启动 treasury agent 服务（脱离会话，日志到 /tmp/opencode/agent.log）
cd "$(dirname "$0")/../agent" || exit 1
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 5060 --env-file .env --log-level info
