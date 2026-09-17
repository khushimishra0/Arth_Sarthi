"""Loan analysis — §5. The model reads the page; `finance.py` works out what it costs."""

from app.features.loan.advisor import Advice, Verdict, advise, load_benchmarks
from app.features.loan.computed import ComputedLoan, compute
from app.features.loan.extractor import ExtractionRoute, extract_terms
from app.features.loan.formatter import format_analysis, format_unreadable
from app.features.loan.hidden import Finding, Severity, detect
from app.features.loan.schema import LoanTerms
from app.features.loan.service import analyse_pdf, analyse_terms, check_document

__all__ = [
    "Advice",
    "ComputedLoan",
    "ExtractionRoute",
    "Finding",
    "LoanTerms",
    "Severity",
    "Verdict",
    "advise",
    "analyse_pdf",
    "analyse_terms",
    "check_document",
    "compute",
    "detect",
    "extract_terms",
    "format_analysis",
    "format_unreadable",
    "load_benchmarks",
]
