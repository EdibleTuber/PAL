from pal.discord_interactions import (
    build_learning_candidate_embed,
    parse_button_custom_id,
)
from agent_core.protocol import LearningCandidateProposalMessage


def _sample() -> LearningCandidateProposalMessage:
    return LearningCandidateProposalMessage(
        proposal_id="lc-1",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        trigger_excerpt="you always try to merge into one article",
    )


def test_learning_candidate_embed_has_expected_fields():
    embed, _ = build_learning_candidate_embed(_sample())
    rendered = (embed.title or "")
    for field in embed.fields:
        rendered += f"\n{field.name}: {field.value}"
    assert "possible learning" in rendered.lower()
    assert "Granularity Over Consolidation" in rendered
    assert "Keep articles focused." in rendered
    assert "you always try to merge" in rendered


def test_learning_candidate_has_two_buttons():
    _, view = build_learning_candidate_embed(_sample())
    labels = [child.label for child in view.children if hasattr(child, "label")]
    assert "Approve" in labels
    assert "Skip" in labels
    assert "Edit" not in labels


def test_parse_button_custom_id_accepts_learning_candidate():
    assert parse_button_custom_id("learning_candidate:approve:xyz") == (
        "learning_candidate", "approve", "xyz",
    )
    assert parse_button_custom_id("learning_candidate:decline:xyz") == (
        "learning_candidate", "decline", "xyz",
    )


def test_learning_candidate_embed_truncates_long_body():
    msg = LearningCandidateProposalMessage(
        proposal_id="x", title="T", body="x" * 5000, trigger_excerpt="y",
    )
    embed, _ = build_learning_candidate_embed(msg)
    body = next(f for f in embed.fields if f.name == "Body")
    assert len(body.value) <= 1024
