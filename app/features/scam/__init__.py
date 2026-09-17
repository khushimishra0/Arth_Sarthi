"""Scam detection — §4. Extraction transcribes, code judges, every point is explained."""

from app.features.scam.formatter import format_assessment, format_unreadable
from app.features.scam.rules import FiredRule, evaluate, load_rules, rule_score
from app.features.scam.schema import ExtractedMessage, ScamJudgement
from app.features.scam.scorer import Band, ScamAssessment, score
from app.features.scam.service import analyse_image, analyse_text, assess, check_screenshot

__all__ = [
    "Band",
    "ExtractedMessage",
    "FiredRule",
    "ScamAssessment",
    "ScamJudgement",
    "analyse_image",
    "analyse_text",
    "assess",
    "check_screenshot",
    "evaluate",
    "format_assessment",
    "format_unreadable",
    "load_rules",
    "rule_score",
    "score",
]
