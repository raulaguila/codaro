"""Compatibility imports. Terminal UI implementations live in codaro.ui."""

from codaro.ui.app import CodaroApp as CodaroApp
from codaro.ui.composer import Prompt as Prompt
from codaro.ui.formatting import short_path as short_path
from codaro.ui.reviews import ChangesReview as ChangesReview
from codaro.ui.reviews import CommandReview as CommandReview
from codaro.ui.reviews import EditReview as EditReview
from codaro.ui.reviews import ExternalToolReview as ExternalToolReview
from codaro.ui.reviews import ScopeReview as ScopeReview
from codaro.ui.widgets import ActivityGroup as ActivityGroup
from codaro.ui.widgets import GenerationPreview as GenerationPreview
from codaro.ui.widgets import ProposalCard as ProposalCard

__all__ = [
    "CodaroApp",
    "Prompt",
    "short_path",
    "EditReview",
    "ChangesReview",
    "ScopeReview",
    "CommandReview",
    "ExternalToolReview",
    "GenerationPreview",
    "ActivityGroup",
    "ProposalCard",
]
