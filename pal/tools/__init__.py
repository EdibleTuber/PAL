"""PAL tool implementations (Tool subclasses).

Phase F migration complete through PR7. Tool subclasses are added per-category
across PRs 2-7 and re-exported here. Tasks owning each migration:
    PR2: vault.py (edit/create/move; read/list/search dropped in favor of
         agent_core builtins cat/ls/grep)
    PR3: research.py (propose_research, research_topic)
    PR4: compile.py, consolidate.py, reorg.py, wait.py
    PR7: scratch.py (update_scratch, add_learning, PAL overrides of framework builtins)
"""
from pal.tools.vault import (
    CreateFile,
    DeleteFile,
    EditFile,
    MoveFile,
    ReplaceInFile,
)
from pal.tools.research import ProposeResearch, ResearchTopic
from pal.tools.compile import CompileSummary, ProposeCompileBatch, CompileBatch
from pal.tools.consolidate import ProposeConsolidate, Consolidate
from pal.tools.url_fix import ProposeUrlFix, UrlFix
from pal.tools.reorg import ProposeReorg, ProposePromote, Reorg
from pal.tools.promote_synthesis import PromoteSynthesisProposal
from pal.tools.wait import WaitForReindex
from pal.tools.scratch import UpdateScratch, AddLearning

__all__ = [
    "CreateFile",
    "DeleteFile",
    "EditFile",
    "MoveFile",
    "ReplaceInFile",
    "ProposeResearch",
    "ResearchTopic",
    "CompileSummary",
    "ProposeCompileBatch",
    "CompileBatch",
    "ProposeConsolidate",
    "Consolidate",
    "ProposeUrlFix",
    "UrlFix",
    "ProposeReorg",
    "ProposePromote",
    "Reorg",
    "PromoteSynthesisProposal",
    "WaitForReindex",
    "UpdateScratch",
    "AddLearning",
]
