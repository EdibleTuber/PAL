from pal.discord_interactions import (
    ProposalContext,
    build_research_proposal_embed,
    build_compile_proposal_embed,
    build_research_edit_modal,
    build_compile_edit_modal,
)
from pal.protocol import ResearchProposalMessage, CompileProposalMessage


def test_research_embed_has_title_and_fields():
    msg = ResearchProposalMessage(
        proposal_id="abc-123",
        topic="prompt injection in MCP",
        depth=3,
        rationale="vault has no sources",
    )
    embed, view = build_research_proposal_embed(msg)
    assert "research" in embed.title.lower()
    field_values = {f.name: f.value for f in embed.fields}
    assert field_values.get("Topic") == "prompt injection in MCP"
    assert field_values.get("Depth") == "3"
    assert field_values.get("Rationale") == "vault has no sources"


def test_research_embed_view_has_three_buttons_with_proposal_id():
    msg = ResearchProposalMessage(
        proposal_id="abc-123",
        topic="t",
        depth=3,
        rationale="r",
    )
    embed, view = build_research_proposal_embed(msg)
    custom_ids = [child.custom_id for child in view.children]
    assert "research:approve:abc-123" in custom_ids
    assert "research:decline:abc-123" in custom_ids
    assert "research:edit:abc-123" in custom_ids


def test_compile_embed_includes_truncated_path_list_and_buttons():
    paths = [f"raw/summaries/file-{i}.md" for i in range(15)]
    msg = CompileProposalMessage(
        proposal_id="xyz",
        summary_paths=paths,
        rationale="promote findings",
    )
    embed, view = build_compile_proposal_embed(msg)
    assert "compile" in embed.title.lower()
    summaries_field = next(
        f for f in embed.fields if "Summaries" in f.name
    )
    assert "raw/summaries/file-0.md" in summaries_field.value
    assert "+5 more" in summaries_field.value or "+5" in summaries_field.value
    custom_ids = [child.custom_id for child in view.children]
    assert "compile:approve:xyz" in custom_ids
    assert "compile:decline:xyz" in custom_ids
    assert "compile:edit:xyz" in custom_ids


def test_proposal_context_preserves_fields():
    ctx = ProposalContext(
        proposal_id="abc",
        kind="research",
        triggerer_id="user-42",
        topic="t",
        depth=3,
        rationale="r",
    )
    assert ctx.proposal_id == "abc"
    assert ctx.kind == "research"
    assert ctx.triggerer_id == "user-42"


def test_proposal_context_for_compile():
    ctx = ProposalContext(
        proposal_id="xyz",
        kind="compile",
        triggerer_id="user-42",
        summary_paths=["raw/summaries/a.md"],
        rationale="r",
    )
    assert ctx.summary_paths == ["raw/summaries/a.md"]


def test_research_edit_modal_has_topic_and_depth_inputs_with_defaults():
    ctx = ProposalContext(
        proposal_id="abc",
        kind="research",
        triggerer_id="u1",
        topic="original topic",
        depth=4,
        rationale="r",
    )
    modal = build_research_edit_modal(ctx)
    assert modal.custom_id == "research:abc"
    labels = [c.label for c in modal.children]
    assert "New topic" in labels
    assert "New depth" in labels
    topic_input = next(c for c in modal.children if c.label == "New topic")
    depth_input = next(c for c in modal.children if c.label == "New depth")
    assert topic_input.default == "original topic"
    assert depth_input.default == "4"


def test_compile_edit_modal_has_paths_input_with_default():
    ctx = ProposalContext(
        proposal_id="xyz",
        kind="compile",
        triggerer_id="u1",
        summary_paths=["raw/summaries/a.md", "raw/summaries/b.md"],
        rationale="r",
    )
    modal = build_compile_edit_modal(ctx)
    assert modal.custom_id == "compile:xyz"
    labels = [c.label for c in modal.children]
    assert any("paths" in l.lower() for l in labels)
    paths_input = modal.children[0]
    assert "raw/summaries/a.md" in paths_input.default
    assert "raw/summaries/b.md" in paths_input.default
