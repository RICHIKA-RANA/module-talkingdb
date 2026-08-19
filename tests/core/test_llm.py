"""Unit tests for app.core.llm's provider-agnostic LiteLLM wrapper."""

import pytest

from app.core import llm


class TestResolveCallKwargs:
    def test_openai_uses_bare_model_name_and_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        kwargs = llm._resolve_call_kwargs("openai")
        assert kwargs == {"model": "gpt-5.4-mini", "api_key": "sk-test"}

    def test_grok_uses_xai_prefix(self, monkeypatch):
        monkeypatch.setenv("GROK_API_KEY", "grok-test")
        kwargs = llm._resolve_call_kwargs("grok")
        assert kwargs["model"] == "xai/grok-4.3"
        assert kwargs["api_key"] == "grok-test"

    def test_cloud_provider_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(llm.LLMNotConfiguredError):
            llm._resolve_call_kwargs("openai")

    def test_ollama_is_local_and_uses_base_url(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        kwargs = llm._resolve_call_kwargs("ollama")
        assert kwargs["model"] == "openai/qwen3:4b"
        assert kwargs["api_key"] == "not-needed"
        assert kwargs["api_base"] == llm.DEFAULT_OLLAMA_BASE_URL

    def test_ollama_honors_custom_base_url(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://custom-host:11434/v1")
        kwargs = llm._resolve_call_kwargs("ollama")
        assert kwargs["api_base"] == "http://custom-host:11434/v1"

    def test_unknown_provider_raises(self):
        with pytest.raises(llm.LLMNotConfiguredError):
            llm._resolve_call_kwargs("not-a-real-provider")


class TestIsConfigured:
    def test_false_when_provider_unset(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        assert llm.is_configured() is False

    def test_false_when_provider_unknown(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
        assert llm.is_configured() is False

    def test_false_when_cloud_key_missing(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert llm.is_configured() is False

    def test_true_when_cloud_key_present(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert llm.is_configured() is True

    def test_true_for_local_provider_without_a_key(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        assert llm.is_configured() is True


class TestGetLLMResponse:
    def test_raises_when_provider_unset(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        with pytest.raises(llm.LLMNotConfiguredError):
            llm.get_llm_response("hello")

    def test_returns_completion_text(self, monkeypatch, mock_llm):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        result = llm.get_llm_response("summarize this")

        assert result == "This is a fake LLM response."
        assert mock_llm[0]["messages"] == [
            {"role": "user", "content": "summarize this"}
        ]

    def test_local_provider_appends_no_think_suffix(self, monkeypatch, mock_llm):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")

        llm.get_llm_response("summarize this")

        assert mock_llm[0]["messages"][0]["content"] == "summarize this /no_think"

    def test_passes_timeout_and_model_kwargs(self, monkeypatch, mock_llm):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        llm.get_llm_response("hello")

        assert mock_llm[0]["timeout"] == 300
        assert mock_llm[0]["model"] == "gpt-5.4-mini"
        assert mock_llm[0]["api_key"] == "sk-test"
