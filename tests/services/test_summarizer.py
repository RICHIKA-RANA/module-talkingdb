"""Unit tests for app.services.summarizer."""

from app.services import summarizer
from app.services.summarizer import (
    MAX_CHARS_PER_ELEMENT,
    MAX_ELEMENTS,
    summarize_elements,
)


class TestSummarizeElements:
    def test_returns_none_when_no_elements(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            summarizer, "get_llm_response", lambda prompt: calls.append(prompt)
        )

        result = summarize_elements("what is this about?", [])

        assert result is None
        assert calls == []

    def test_returns_llm_response_when_elements_present(self, monkeypatch):
        monkeypatch.setattr(
            summarizer, "get_llm_response", lambda prompt: "a fake summary"
        )

        result = summarize_elements(
            "what is this about?", [{"content": "hello world"}]
        )

        assert result == "a fake summary"

    def test_prompt_includes_query_and_element_content(self, monkeypatch):
        captured = {}

        def _fake(prompt):
            captured["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(summarizer, "get_llm_response", _fake)

        summarize_elements(
            "how does X work?", [{"content": "X works by doing Y."}]
        )

        assert "how does X work?" in captured["prompt"]
        assert "X works by doing Y." in captured["prompt"]

    def test_elements_missing_or_blank_content_are_skipped(self, monkeypatch):
        captured = {}

        def _fake(prompt):
            captured["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(summarizer, "get_llm_response", _fake)

        summarize_elements(
            "q",
            [
                {"content": None},
                {"content": "   "},
                {},
                {"content": "real content"},
            ],
        )

        # Only the one real chunk should be numbered [1].
        assert "[1] real content" in captured["prompt"]
        assert "[2]" not in captured["prompt"]

    def test_truncates_to_max_elements(self, monkeypatch):
        captured = {}

        def _fake(prompt):
            captured["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(summarizer, "get_llm_response", _fake)

        elements = [
            {"content": f"chunk-{i}"} for i in range(MAX_ELEMENTS + 5)
        ]
        summarize_elements("q", elements)

        prompt = captured["prompt"]
        assert f"chunk-{MAX_ELEMENTS - 1}" in prompt
        assert f"chunk-{MAX_ELEMENTS}" not in prompt

    def test_truncates_each_element_to_max_chars(self, monkeypatch):
        captured = {}

        def _fake(prompt):
            captured["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(summarizer, "get_llm_response", _fake)

        long_content = "x" * (MAX_CHARS_PER_ELEMENT + 100)
        summarize_elements("q", [{"content": long_content}])

        prompt = captured["prompt"]
        assert "x" * MAX_CHARS_PER_ELEMENT in prompt
        assert "x" * (MAX_CHARS_PER_ELEMENT + 1) not in prompt
