import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pal.discord_interactions import (
    DiscordStreamProcessor,
    ProposalContext,
    build_research_proposal_embed,
    build_compile_proposal_embed,
    build_research_edit_modal,
    build_compile_edit_modal,
)
from pal.protocol import (
    ResearchProposalMessage,
    CompileProposalMessage,
    ResponseMessage,
    StreamChunkMessage,
    ToolProgressMessage,
)


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


def test_compile_embed_includes_path_list_and_buttons():
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
    # Short paths all fit within the character budget; first path must be shown.
    assert "raw/summaries/file-0.md" in summaries_field.value
    assert len(summaries_field.value) <= 1024
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


@pytest.mark.asyncio
async def test_stream_processor_plain_chat_returns_final_text():
    """Non-proposal chat: accumulate progress, return (progress, final_text).
    Matches the legacy collect_response shape."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ToolProgressMessage(tool="read_file", arguments={"path": "foo.md"})
        yield StreamChunkMessage(token="Hello ")
        yield StreamChunkMessage(token="world")
        yield ResponseMessage(text="")

    progress, final_text = await processor.run(stream())
    assert final_text == "Hello world"
    assert len(progress) == 1
    assert progress[0].tool == "read_file"
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_stream_processor_posts_research_proposal_and_records_context():
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    bot.connections = MagicMock()
    client = MagicMock()

    posted_message = MagicMock()
    posted_message.id = 555
    posted_message.create_thread = AsyncMock()
    channel.send = AsyncMock(return_value=posted_message)
    channel.id = 100

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="user-1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ResearchProposalMessage(
            proposal_id="abc",
            topic="t",
            depth=3,
            rationale="r",
        )
        yield ResponseMessage(text="")

    progress, final_text = await processor.run(stream())
    channel.send.assert_awaited_once()
    ctx = bot.active_proposals.get("abc")
    assert ctx is not None
    assert ctx.kind == "research"
    assert ctx.triggerer_id == "user-1"
    assert ctx.topic == "t"
    assert ctx.discord_message_id == 555
    assert ctx.channel_id == 100


@pytest.mark.asyncio
async def test_stream_processor_posts_progress_to_thread_after_proposal():
    """Once a proposal has been posted, subsequent progress events route
    to the thread (created lazily on first progress event), not the
    channel's main stream."""
    channel = MagicMock()
    bot = MagicMock()
    bot.active_proposals = {}
    client = MagicMock()

    thread_mock = MagicMock()
    thread_mock.send = AsyncMock()
    posted_message = MagicMock()
    posted_message.id = 777
    posted_message.create_thread = AsyncMock(return_value=thread_mock)
    channel.send = AsyncMock(return_value=posted_message)
    channel.id = 200

    processor = DiscordStreamProcessor(
        channel=channel,
        triggerer_id="u1",
        bot=bot,
        client=client,
    )

    async def stream():
        yield ResearchProposalMessage(
            proposal_id="p1", topic="t", depth=3, rationale="r",
        )
        yield ToolProgressMessage(
            tool="research_topic",
            arguments={"status": "Researching: t"},
        )
        yield ResponseMessage(text="done")

    progress, final_text = await processor.run(stream())
    posted_message.create_thread.assert_awaited_once()
    thread_mock.send.assert_awaited()
    assert progress == []
    assert final_text == "done"


from pal.discord_interactions import (
    parse_button_custom_id,
    parse_modal_custom_id,
    extract_modal_field_values,
)


def test_parse_button_custom_id_research_approve():
    kind, action, proposal_id = parse_button_custom_id("research:approve:abc-123")
    assert kind == "research"
    assert action == "approve"
    assert proposal_id == "abc-123"


def test_parse_button_custom_id_compile_edit():
    kind, action, proposal_id = parse_button_custom_id("compile:edit:xyz")
    assert kind == "compile"
    assert action == "edit"
    assert proposal_id == "xyz"


def test_parse_button_custom_id_invalid_returns_none():
    assert parse_button_custom_id("") is None
    assert parse_button_custom_id("bogus") is None
    assert parse_button_custom_id("research:approve") is None
    assert parse_button_custom_id("wrong:kind:abc") is None


def test_parse_modal_custom_id():
    kind, proposal_id = parse_modal_custom_id("research:abc-123")
    assert kind == "research"
    assert proposal_id == "abc-123"
    kind, proposal_id = parse_modal_custom_id("compile:xyz")
    assert kind == "compile"
    assert proposal_id == "xyz"


def test_parse_modal_custom_id_invalid_returns_none():
    assert parse_modal_custom_id("bogus") is None
    assert parse_modal_custom_id("") is None


def test_extract_modal_field_values_multi_field():
    """Given discord.py's modal submit interaction.data shape, extract
    the text values in order, one per action-row."""
    interaction_data = {
        "components": [
            {"components": [{"value": "new topic text"}]},
            {"components": [{"value": "5"}]},
        ],
    }
    values = extract_modal_field_values(interaction_data)
    assert values == ["new topic text", "5"]


def test_extract_modal_field_values_single_field():
    interaction_data = {
        "components": [
            {"components": [{"value": "path1\npath2\npath3"}]},
        ],
    }
    values = extract_modal_field_values(interaction_data)
    assert values == ["path1\npath2\npath3"]


def test_extract_modal_field_values_handles_missing():
    assert extract_modal_field_values({}) == []
    assert extract_modal_field_values({"components": []}) == []


from pal.discord_interactions import build_consolidate_proposal_embed, build_reorg_proposal_embed
from pal.protocol import ConsolidateProposalMessage, ReorgProposalMessage


def test_reorg_embed_includes_operations_and_references_count():
    msg = ReorgProposalMessage(
        proposal_id="xyz-1",
        operations=[
            {"type": "move", "src": "A.md", "dst": "B.md"},
            {"type": "merge", "src": "C.md", "dst": "D.md"},
        ],
        rationale="test rationale",
        references_preview=4,
    )
    embed, view = build_reorg_proposal_embed(msg)
    assert "reorg" in embed.title.lower()
    ops_field = next(f for f in embed.fields if "Operations" in f.name)
    assert "[move]" in ops_field.value
    assert "[merge]" in ops_field.value
    assert "A.md" in ops_field.value
    assert "B.md" in ops_field.value
    refs_field = next(f for f in embed.fields if "rewrite" in f.name.lower() or "link" in f.name.lower())
    assert "4" in refs_field.value

    custom_ids = [child.custom_id for child in view.children]
    assert "reorg:approve:xyz-1" in custom_ids
    assert "reorg:decline:xyz-1" in custom_ids
    assert "reorg:edit:xyz-1" in custom_ids


def test_reorg_embed_truncates_long_operation_lists():
    # Use paths long enough to exhaust the character budget within 15 ops.
    ops = [
        {"type": "move", "src": f"Category/{'a' * 60}-{i}.md", "dst": f"Category/{'b' * 60}-{i}.md"}
        for i in range(15)
    ]
    msg = ReorgProposalMessage(
        proposal_id="p1",
        operations=ops,
        rationale="r",
        references_preview=0,
    )
    embed, view = build_reorg_proposal_embed(msg)
    ops_field = next(f for f in embed.fields if "Operations" in f.name)
    assert len(ops_field.value) <= 1024
    assert "more" in ops_field.value


def test_reorg_embed_fits_under_discord_field_limit():
    """Ops with very long paths must not exceed Discord's 1024-char field limit."""
    long_src = "AI-Agents/" + ("x" * 300) + ".md"
    long_dst = "AI-Agents/" + ("y" * 300) + ".md"
    ops = [
        {"type": "move", "src": long_src, "dst": long_dst}
        for _ in range(10)
    ]
    msg = ReorgProposalMessage(
        proposal_id="p1",
        operations=ops,
        rationale="r",
        references_preview=0,
    )
    embed, _ = build_reorg_proposal_embed(msg)
    ops_field = next(f for f in embed.fields if "Operations" in f.name)
    assert len(ops_field.value) <= 1024, (
        f"ops field is {len(ops_field.value)} chars; Discord limit is 1024"
    )
    # Should still include at least one operation
    assert "[move]" in ops_field.value
    # And indicate truncation
    assert "more" in ops_field.value


def test_compile_embed_fits_under_discord_field_limit():
    """Compile summaries with very long paths must not exceed 1024 chars."""
    long_paths = [f"raw/summaries/{'x' * 300}-{i}.md" for i in range(10)]
    msg = CompileProposalMessage(
        proposal_id="p2",
        summary_paths=long_paths,
        rationale="r",
    )
    embed, _ = build_compile_proposal_embed(msg)
    summaries_field = next(f for f in embed.fields if "Summaries" in f.name)
    assert len(summaries_field.value) <= 1024
    assert "more" in summaries_field.value


def test_parse_button_custom_id_accepts_consolidate():
    assert parse_button_custom_id("consolidate:approve:abc-1") == ("consolidate", "approve", "abc-1")
    assert parse_button_custom_id("consolidate:decline:abc-1") == ("consolidate", "decline", "abc-1")


def test_build_consolidate_proposal_embed():
    msg = ConsolidateProposalMessage(
        proposal_id="abc-1",
        source_paths=["Security/a.md", "Security/b.md"],
        target_path="Security/Combined.md",
        target_title="Combined",
        rationale="merge overlapping notes",
    )
    embed, view = build_consolidate_proposal_embed(msg)
    assert embed.title == "PAL proposes consolidate"
    # sources are rendered as a field
    field_names = [f.name for f in embed.fields]
    assert any("Sources" in n for n in field_names)
    assert any("Target" in n for n in field_names)
    # buttons carry the consolidate kind
    ids = [item.custom_id for item in view.children]
    assert "consolidate:approve:abc-1" in ids
    assert "consolidate:decline:abc-1" in ids
    assert "consolidate:edit:abc-1" in ids
