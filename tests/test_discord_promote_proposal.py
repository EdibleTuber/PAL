from pal.discord_interactions import build_promote_proposal_embed, parse_button_custom_id
from pal.protocol import PromoteProposalMessage


def _sample() -> PromoteProposalMessage:
    return PromoteProposalMessage(
        proposal_id="p1",
        slug="granularity",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        rationale="User reiterated.",
    )


def test_promote_embed_contains_title_body_rationale_slug():
    embed, _ = build_promote_proposal_embed(_sample())
    rendered = (embed.title or "")
    for field in embed.fields:
        rendered += f"\n{field.name}: {field.value}"
    assert "Granularity Over Consolidation" in rendered
    assert "Keep articles focused." in rendered
    assert "User reiterated." in rendered
    assert "granularity" in rendered


def test_promote_embed_has_two_buttons_no_edit():
    _, view = build_promote_proposal_embed(_sample())
    labels = [child.label for child in view.children if hasattr(child, "label")]
    assert "Approve" in labels
    assert "Decline" in labels
    assert "Edit" not in labels


def test_promote_embed_button_custom_ids_parse_correctly():
    _, view = build_promote_proposal_embed(_sample())
    for child in view.children:
        if hasattr(child, "custom_id"):
            parsed = parse_button_custom_id(child.custom_id)
            assert parsed is not None
            kind, action, pid = parsed
            assert kind == "promote"
            assert action in ("approve", "decline")
            assert pid == "p1"


def test_parse_button_custom_id_accepts_promote():
    assert parse_button_custom_id("promote:approve:xyz") == ("promote", "approve", "xyz")
    assert parse_button_custom_id("promote:decline:xyz") == ("promote", "decline", "xyz")


def test_promote_embed_truncates_long_body():
    msg = PromoteProposalMessage(
        proposal_id="p1",
        slug="s",
        title="T",
        body="x" * 5000,
        rationale="r",
    )
    embed, _ = build_promote_proposal_embed(msg)
    body_field = next(f for f in embed.fields if f.name == "Body")
    assert len(body_field.value) <= 1024  # Discord field limit
