"""Government schemes — §6. Every scheme is a verified row; the model only explains."""

from app.features.schemes.eligibility import eligible_schemes, explain_rejection, is_eligible
from app.features.schemes.explainer import explain, verify_explanations
from app.features.schemes.formatter import format_matches, format_no_matches
from app.features.schemes.ranker import MAX_RESULTS, Ranked, rank_schemes
from app.features.schemes.schema import Scheme, SchemeProfile, load_schemes
from app.features.schemes.service import find_schemes, match_profile

__all__ = [
    "MAX_RESULTS",
    "Ranked",
    "Scheme",
    "SchemeProfile",
    "eligible_schemes",
    "explain",
    "explain_rejection",
    "find_schemes",
    "format_matches",
    "format_no_matches",
    "is_eligible",
    "load_schemes",
    "match_profile",
    "rank_schemes",
    "verify_explanations",
]
