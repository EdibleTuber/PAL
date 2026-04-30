from agent_core.protocol import encode_message, decode_message
from pal.protocol import PromoteProposalMessage


def test_promote_proposal_roundtrip():
    msg = PromoteProposalMessage(
        proposal_id="p-123",
        slug="granularity-over-consolidation",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        rationale="User reiterated the preference.",
    )
    wire = encode_message(msg)
    decoded = decode_message(wire)
    assert isinstance(decoded, PromoteProposalMessage)
    assert decoded.proposal_id == "p-123"
    assert decoded.slug == "granularity-over-consolidation"
    assert decoded.title == "Granularity Over Consolidation"
    assert decoded.body == "Keep articles focused."
    assert decoded.rationale == "User reiterated the preference."


def test_promote_proposal_has_type_discriminator():
    import json
    from dataclasses import asdict
    msg = PromoteProposalMessage(
        proposal_id="x", slug="y", title="z", body="b", rationale="r",
    )
    serialized = json.loads(encode_message(msg).decode())
    assert serialized["type"] == "promote_proposal"
