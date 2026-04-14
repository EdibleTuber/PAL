from pal.cli import format_research_proposal, _tool_progress_label
from pal.protocol import ResearchProposalMessage


def test_format_research_proposal_includes_topic_depth_rationale():
    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="prompt injection in MCP",
        depth=3,
        rationale="vault is empty on this",
    )
    text = format_research_proposal(msg)
    assert "prompt injection in MCP" in text
    assert "3" in text
    assert "vault is empty on this" in text
    assert "[a]" in text.lower() or "approve" in text.lower()


def test_research_topic_progress_uses_status_text():
    label = _tool_progress_label("research_topic", {"status": "Fetching 3 sources for: foo"})
    assert label == "[Fetching 3 sources for: foo]"


def test_research_topic_progress_without_status_falls_back():
    label = _tool_progress_label("research_topic", {})
    assert "research" in label.lower()


def test_propose_research_progress_with_topic():
    label = _tool_progress_label("propose_research", {"topic": "neural networks"})
    assert label == '[proposing research on "neural networks"...]'


def test_propose_research_progress_without_topic():
    label = _tool_progress_label("propose_research", {})
    assert "proposing research" in label.lower()


def test_search_web_progress_with_query():
    label = _tool_progress_label("search_web", {"query": "machine learning"})
    assert label == '[searching web for "machine learning"...]'


def test_search_web_progress_without_query():
    label = _tool_progress_label("search_web", {})
    assert "searching web" in label.lower()


def test_format_compile_proposal_includes_paths_and_rationale():
    from pal.cli import format_compile_proposal
    from pal.protocol import CompileProposalMessage
    msg = CompileProposalMessage(
        proposal_id="abc",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="promote home-automation research findings",
    )
    text = format_compile_proposal(msg)
    assert "raw/summaries/a.md" in text
    assert "raw/summaries/b.md" in text
    assert "promote home-automation research findings" in text
    assert "[a]" in text.lower() or "approve" in text.lower()
