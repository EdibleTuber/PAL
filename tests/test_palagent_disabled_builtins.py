"""Verify PALAgent's disabled_builtins configuration."""
from __future__ import annotations

from agent_core.tools.builtin import BUILTIN_TOOLS

from pal.agent import PALAgent


def test_palagent_disables_search_web():
    """PALAgent should disable the search_web builtin tool.

    Rationale: search_web's output URLs are mostly unfetchable (allowlist
    mismatch with FetchUrl), and the user prefers propose_research for web
    work. See docs/superpowers/specs/2026-05-12-search-vault-json-result-format-design.md.
    """
    assert "search_web" in PALAgent.disabled_builtins


def test_palagent_search_web_not_in_active_tool_registry():
    """After tool registration, search_web should not be callable by the LLM."""
    disabled = PALAgent.disabled_builtins
    active = [t for t in BUILTIN_TOOLS if t.name not in disabled]
    active_names = {t.name for t in active}
    assert "search_web" not in active_names
    # Sanity: search_vault should still be active.
    assert "search_vault" in active_names
