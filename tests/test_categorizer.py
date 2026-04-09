"""Unit tests for auto-categorization."""
from pathlib import Path

import pytest

from pal.categorizer import Categorizer, build_categorization_prompt, parse_category_response
from pal.inference import InferenceClient, CompletionResult


class TestParseCategoryResponse:
    def test_simple_directory(self):
        assert parse_category_response("Research") == "Research"

    def test_nested_directory(self):
        assert parse_category_response("Projects/infrastructure") == "Projects/infrastructure"

    def test_strips_whitespace(self):
        assert parse_category_response("  Research  \n") == "Research"

    def test_strips_leading_slash(self):
        assert parse_category_response("/Research") == "Research"

    def test_strips_trailing_slash(self):
        assert parse_category_response("Research/") == "Research"

    def test_rejects_system_directory(self):
        assert parse_category_response("_wisdom") == "Research"

    def test_rejects_system_nested(self):
        assert parse_category_response("_config/allowlist") == "Research"

    def test_rejects_path_traversal(self):
        assert parse_category_response("../etc") == "Research"

    def test_rejects_empty(self):
        assert parse_category_response("") == "Research"

    def test_rejects_raw(self):
        assert parse_category_response("raw") == "Research"

    def test_rejects_raw_nested(self):
        assert parse_category_response("raw/web") == "Research"


class TestBuildCategorizationPrompt:
    def test_includes_title(self):
        prompt = build_categorization_prompt("Quantum Computing", "Qubits are...", ["Research", "Projects"])
        assert "Quantum Computing" in prompt

    def test_includes_preview(self):
        prompt = build_categorization_prompt("Title", "Some preview text", ["Research"])
        assert "Some preview text" in prompt

    def test_includes_directories(self):
        prompt = build_categorization_prompt("Title", "Preview", ["Research", "Projects", "Notes"])
        assert "Research" in prompt
        assert "Projects" in prompt
        assert "Notes" in prompt

    def test_truncates_preview(self):
        long_text = "word " * 500
        prompt = build_categorization_prompt("Title", long_text, ["Research"])
        # Preview should be truncated, not the full 500 words
        assert len(prompt) < len(long_text)


class TestCategorizer:
    @pytest.mark.asyncio
    async def test_categorize_returns_model_choice(self):
        async def fake_complete(messages, **kwargs):
            return CompletionResult(type="text", content="Projects/infrastructure")

        inference = InferenceClient(base_url="http://unused", model="unused")
        inference.complete = fake_complete

        categorizer = Categorizer(inference)
        result = await categorizer.categorize(
            title="Server Setup Guide",
            body="This guide covers setting up the inference server...",
            vault_path=Path("/tmp/vault"),
        )
        assert result == "Projects/infrastructure"

    @pytest.mark.asyncio
    async def test_categorize_falls_back_on_error(self):
        async def broken_complete(messages, **kwargs):
            raise RuntimeError("LLM down")

        inference = InferenceClient(base_url="http://unused", model="unused")
        inference.complete = broken_complete

        categorizer = Categorizer(inference)
        result = await categorizer.categorize(
            title="Anything",
            body="Anything",
            vault_path=Path("/tmp/vault"),
        )
        assert result == "Research"

    @pytest.mark.asyncio
    async def test_categorize_lists_vault_directories(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Research").mkdir(parents=True)
        (vault / "Projects").mkdir()
        (vault / "_wisdom").mkdir()  # system dir, should be excluded
        (vault / "raw").mkdir()       # raw dir, should be excluded

        prompts_seen = []

        async def spy_complete(messages, **kwargs):
            prompts_seen.append(messages[-1]["content"])
            return CompletionResult(type="text", content="Research")

        inference = InferenceClient(base_url="http://unused", model="unused")
        inference.complete = spy_complete

        categorizer = Categorizer(inference)
        await categorizer.categorize("Title", "Body", vault)

        prompt = prompts_seen[0]
        assert "Research" in prompt
        assert "Projects" in prompt
        assert "_wisdom" not in prompt
        assert "raw" not in prompt
