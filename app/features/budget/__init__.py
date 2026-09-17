"""Budget coach — §7. No AI anywhere: the user types numbers, Python divides them."""

from app.features.budget.analyser import BudgetAnalysis, analyse
from app.features.budget.chart import render_chart
from app.features.budget.formatter import format_analysis, format_needs_more
from app.features.budget.goals import Goal, GoalKind, SavingsPlan, build_plan
from app.features.budget.parser import ParseResult, parse, parse_amount
from app.features.budget.schema import Budget, CategoryBand, CategoryVerdict, load_benchmarks
from app.features.budget.service import analyse_budget, check_budget_text, run_budget

__all__ = [
    "Budget",
    "BudgetAnalysis",
    "CategoryBand",
    "CategoryVerdict",
    "Goal",
    "GoalKind",
    "ParseResult",
    "SavingsPlan",
    "analyse",
    "analyse_budget",
    "build_plan",
    "check_budget_text",
    "format_analysis",
    "format_needs_more",
    "load_benchmarks",
    "parse",
    "parse_amount",
    "render_chart",
    "run_budget",
]
