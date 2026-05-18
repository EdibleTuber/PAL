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


def test_format_reorg_proposal_includes_ops_and_preview():
    from pal.cli import format_reorg_proposal
    from pal.protocol import ReorgProposalMessage
    msg = ReorgProposalMessage(
        proposal_id="xyz",
        operations=[
            {"type": "move", "src": "AI-Agents/old.md", "dst": "AI-Agents/new.md"},
            {"type": "merge", "src": "AI-Security/a.md", "dst": "AI-Security/b.md"},
        ],
        rationale="clean up names and dedupe",
        references_preview=5,
    )
    text = format_reorg_proposal(msg)
    assert "reorg" in text.lower()
    assert "[move]" in text
    assert "[merge]" in text
    assert "AI-Agents/old.md" in text
    assert "AI-Agents/new.md" in text
    assert "AI-Security/a.md" in text
    assert "AI-Security/b.md" in text
    assert "clean up names and dedupe" in text
    assert "5" in text  # references_preview
    assert "[a]" in text.lower() or "approve" in text.lower()


def test_format_research_proposal_renders_topics_list():
    """CLI prompt shows the full topic list when topics is set."""
    from pal.cli import format_research_proposal
    from pal.protocol import ResearchProposalMessage

    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="3 topics: a, b, c",
        depth=3,
        rationale="batch test",
        topics=["docker networking", "k8s ingress", "service mesh"],
    )
    prompt = format_research_proposal(msg)
    assert "docker networking" in prompt
    assert "k8s ingress" in prompt
    assert "service mesh" in prompt
    assert "Depth:     3" in prompt
    assert "Rationale: batch test" in prompt


def test_format_research_proposal_single_topic_unchanged():
    """Regression: single-topic CLI prompt shows the topic on Topic: line."""
    from pal.cli import format_research_proposal
    from pal.protocol import ResearchProposalMessage

    msg = ResearchProposalMessage(
        proposal_id="abc",
        topic="docker networking",
        depth=3,
        rationale="single",
    )
    prompt = format_research_proposal(msg)
    assert "Topic:     docker networking" in prompt
    # Multi-topic 'Topics:' header should NOT appear in single mode
    assert "Topics" not in prompt
