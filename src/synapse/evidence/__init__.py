"""Evidence and citation layer."""

from synapse.evidence.assemble import assemble_pack, detect_conflicts
from synapse.evidence.frame import frame_retrieved_data
from synapse.evidence.ground import ground_citations
from synapse.evidence.types import Citation, ConflictNote, EvidenceItem, EvidencePack, SourceKind

__all__ = [
    "Citation",
    "ConflictNote",
    "EvidenceItem",
    "EvidencePack",
    "SourceKind",
    "assemble_pack",
    "detect_conflicts",
    "frame_retrieved_data",
    "ground_citations",
]
