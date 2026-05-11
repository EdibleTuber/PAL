from agent_core.protocol.transport import _MESSAGE_TYPES as MESSAGE_REGISTRY
from pal.protocol import PromoteSynthesisProposalMessage


def test_promote_synthesis_proposal_message_fields():
    msg = PromoteSynthesisProposalMessage(
        proposal_id="abc",
        title="Vibe-coding",
        rationale="user asked",
        note_path="raw/notes/vibe-coding.md",
        note_body_preview="## Overview\nfoo...",
    )
    assert msg.type == "promote_synthesis_proposal"
    assert msg.proposal_id == "abc"
    assert msg.note_path == "raw/notes/vibe-coding.md"


def test_promote_synthesis_proposal_message_registered():
    assert "promote_synthesis_proposal" in MESSAGE_REGISTRY
