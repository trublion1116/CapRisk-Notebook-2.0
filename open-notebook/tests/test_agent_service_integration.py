"""Tests for the external agent service integration.

Covers:
1. POST /api/insights - direct insight creation endpoint (used by the
   deepagents-based agent service to write insights back into OpenNotebook).
2. graphs/chat.py proxy behavior - when OPEN_NOTEBOOK_AGENT_URL is set, the
   chat turn is proxied to the external service instead of provisioning a
   local model; failures surface as ExternalServiceError.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


def _source(source_id="source:abc"):
    source = MagicMock()
    source.id = source_id
    source.add_insight = AsyncMock(return_value="command:123")
    return source


# ============================================================================
# POST /api/insights
# ============================================================================


class TestCreateInsightEndpoint:
    @patch("api.routers.insights.Source.get", new_callable=AsyncMock)
    def test_creates_insight_via_add_insight(self, mock_get, client):
        source = _source()
        mock_get.return_value = source

        response = client.post(
            "/api/insights",
            json={
                "source_id": "source:abc",
                "insight_type": "核心观点",
                "content": "> quote\n\nanalysis",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending"
        assert body["source_id"] == "source:abc"
        assert body["insight_type"] == "核心观点"
        assert body["command_id"] == "command:123"
        source.add_insight.assert_awaited_once_with("核心观点", "> quote\n\nanalysis")

    @patch("api.routers.insights.Source.get", new_callable=AsyncMock)
    def test_returns_404_for_missing_source(self, mock_get, client):
        mock_get.return_value = None

        response = client.post(
            "/api/insights",
            json={
                "source_id": "source:missing",
                "insight_type": "核心观点",
                "content": "content",
            },
        )

        assert response.status_code == 404


# ============================================================================
# graphs/chat.py agent proxy
# ============================================================================


class TestChatGraphAgentProxy:
    def _state(self):
        from langchain_core.messages import HumanMessage

        return {"messages": [HumanMessage(content="hello")], "notebook": None}

    def _invoke_node(self, config=None):
        import open_notebook.graphs.chat as chat_module

        return chat_module.call_model_with_messages(
            self._state(), config or {"configurable": {"thread_id": "s"}}
        )

    def test_proxies_when_agent_url_set(self, monkeypatch):
        import open_notebook.graphs.chat as chat_module

        monkeypatch.setattr(chat_module, "AGENT_SERVICE_URL", "http://agent:5060")
        monkeypatch.setattr(chat_module, "AGENT_SERVICE_TOKEN", "secret")
        proxy = MagicMock()
        proxy.return_value = "来自 agent 的回答"
        monkeypatch.setattr(chat_module, "_invoke_agent_service", proxy)

        result = self._invoke_node({"configurable": {"thread_id": "chat_session:1"}})

        proxy.assert_called_once()
        args = proxy.call_args[0]
        # system_prompt, messages, thread_id - thread id must come from config
        assert args[2] == "chat_session:1"
        assert result["messages"].type == "ai"
        assert result["messages"].content == "来自 agent 的回答"

    def test_agent_service_error_raises_external_service_error(self, monkeypatch):
        import open_notebook.graphs.chat as chat_module
        from open_notebook.exceptions import ExternalServiceError

        monkeypatch.setattr(chat_module, "AGENT_SERVICE_URL", "http://agent:5060")

        def raise_error(*args, **kwargs):
            raise ExternalServiceError("Agent service request failed")

        monkeypatch.setattr(chat_module, "_invoke_agent_service", raise_error)

        with pytest.raises(ExternalServiceError):
            self._invoke_node()

    def test_no_proxy_without_agent_url(self, monkeypatch):
        import open_notebook.graphs.chat as chat_module

        monkeypatch.setattr(chat_module, "AGENT_SERVICE_URL", None)
        proxy = MagicMock()
        monkeypatch.setattr(chat_module, "_invoke_agent_service", proxy)

        # The real node would provision a model; we only assert the proxy is
        # not touched when AGENT_SERVICE_URL is unset. Patch the provisioner
        # so the node can run without DB/model access.
        model = MagicMock()
        ai_message = MagicMock()
        ai_message.content = "local model answer"
        ai_message.model_copy.return_value = ai_message
        model.invoke.return_value = ai_message
        monkeypatch.setattr(
            chat_module, "provision_langchain_model", AsyncMock(return_value=model)
        )

        result = self._invoke_node()
        proxy.assert_not_called()
        assert result["messages"].content == "local model answer"


# ============================================================================
# graphs/source_chat.py agent proxy
# ============================================================================


class TestSourceChatGraphAgentProxy:
    def _state(self):
        from langchain_core.messages import HumanMessage

        return {
            "messages": [HumanMessage(content="这份报告的核心风险是什么")],
            "source_id": "source:abc",
        }

    def _invoke_node(self, config=None):
        import open_notebook.graphs.source_chat as sc

        return sc.call_model_with_source_context(
            self._state(), config or {"configurable": {"thread_id": "chat_session:9"}}
        )

    @pytest.fixture
    def empty_context(self, monkeypatch):
        from open_notebook.graphs import source_chat as sc

        async def fake_build(*args, **kwargs):
            return {"sources": [], "insights": []}

        monkeypatch.setattr(sc, "build_source_context", fake_build)
        return fake_build

    def test_proxies_when_agent_url_set(self, empty_context, monkeypatch):
        from open_notebook.graphs import source_chat as sc

        monkeypatch.setattr(sc, "AGENT_SERVICE_URL", "http://agent:5060")
        proxy = MagicMock()
        proxy.return_value = "来自 agent 的回答"
        monkeypatch.setattr(sc, "_invoke_agent_service", proxy)

        result = self._invoke_node({"configurable": {"thread_id": "chat_session:9"}})

        proxy.assert_called_once()
        args = proxy.call_args[0]
        assert args[2] == "chat_session:9"
        # system prompt carries the rendered source_chat template
        assert "source" in args[0].lower()
        assert result["messages"].type == "ai"
        assert result["messages"].content == "来自 agent 的回答"
        # context state is still returned for the UI indicators
        assert result["context_indicators"] == {
            "sources": [],
            "insights": [],
            "notes": [],
        }

    def test_agent_service_error_raises_external_service_error(
        self, empty_context, monkeypatch
    ):
        from open_notebook.exceptions import ExternalServiceError
        from open_notebook.graphs import source_chat as sc

        monkeypatch.setattr(sc, "AGENT_SERVICE_URL", "http://agent:5060")

        def raise_error(*args, **kwargs):
            raise ExternalServiceError("Agent service request failed")

        monkeypatch.setattr(sc, "_invoke_agent_service", raise_error)

        with pytest.raises(ExternalServiceError):
            self._invoke_node()

    def test_no_proxy_without_agent_url(self, empty_context, monkeypatch):
        from open_notebook.graphs import source_chat as sc

        monkeypatch.setattr(sc, "AGENT_SERVICE_URL", None)
        proxy = MagicMock()
        monkeypatch.setattr(sc, "_invoke_agent_service", proxy)

        model = MagicMock()
        ai_message = MagicMock()
        ai_message.content = "local model answer"
        ai_message.model_copy.return_value = ai_message
        model.invoke.return_value = ai_message
        monkeypatch.setattr(
            sc, "provision_langchain_model", AsyncMock(return_value=model)
        )

        result = self._invoke_node()
        proxy.assert_not_called()
        assert result["messages"].content == "local model answer"
