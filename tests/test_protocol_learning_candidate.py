from pal.protocol import (
    LearningCandidateProposalMessage,
    encode_message,
    decode_message,
)


def test_learning_candidate_roundtrip():
    msg = LearningCandidateProposalMessage(
        proposal_id="lc-1",
        title="Granularity Over Consolidation",
        body="Keep articles focused.",
        trigger_excerpt="you always try to merge into one article",
    )
    wire = encode_message(msg)
    decoded = decode_message(wire)
    assert isinstance(decoded, LearningCandidateProposalMessage)
    assert decoded.proposal_id == "lc-1"
    assert decoded.title == "Granularity Over Consolidation"
    assert decoded.body == "Keep articles focused."
    assert decoded.trigger_excerpt == "you always try to merge into one article"


def test_learning_candidate_type_discriminator():
    import json
    msg = LearningCandidateProposalMessage(
        proposal_id="x", title="t", body="b", trigger_excerpt="e",
    )
    payload = json.loads(encode_message(msg).decode())
    assert payload["type"] == "learning_candidate_proposal"
