.PHONY: dev test lint fmt

PORT ?= 5060
HOST ?= 0.0.0.0

# 开发模式启动（热重载）
dev: .env
	uv run uvicorn app.main:app --host $(HOST) --port $(PORT) --env-file .env --reload

.env:
	cp .env.example .env
	@echo "已从 .env.example 创建 .env —— 请先填入 LLM_API_KEY 再使用。"

test:
	uv run pytest tests/ -q

lint:
	uv run ruff check app/ tests/

fmt:
	uv run ruff format app/ tests/
	uv run ruff check --fix app/ tests/
