"""
Model router — maps task types to the cheapest model that can do the job well.
Priority scoring uses CVSS-weighted impact vs token cost ratio.
"""

import yaml
from pathlib import Path
from typing import Optional

# Task type → model tier (overridden by budget pressure)
TASK_MODEL_MAP: dict[str, str] = {
    # Haiku — cheap, fast, mechanical
    "tool_output_parsing":      "claude-haiku-4-5",
    "coverage_tracking":        "claude-haiku-4-5",
    "context_compression":      "claude-haiku-4-5",
    "deduplication_check":      "claude-haiku-4-5",
    "phase_gate_validation":    "claude-haiku-4-5",
    "tool_output_compression":  "claude-haiku-4-5",
    "checkpoint_save":          "claude-haiku-4-5",
    "token_reporting":          "claude-haiku-4-5",
    "scope_registration":       "claude-haiku-4-5",

    # Sonnet — analytical work, moderate complexity
    "reconnaissance":           "claude-sonnet-4-6",
    "info_gathering":           "claude-sonnet-4-6",
    "configuration_testing":    "claude-sonnet-4-6",
    "auth_testing":             "claude-sonnet-4-6",
    "session_testing":          "claude-sonnet-4-6",
    "vulnerability_analysis":   "claude-sonnet-4-6",
    "canary_payload_testing":   "claude-sonnet-4-6",
    "business_logic_testing":   "claude-sonnet-4-6",
    "client_side_testing":      "claude-sonnet-4-6",
    "error_handling_testing":   "claude-sonnet-4-6",
    "crypto_testing":           "claude-sonnet-4-6",
    "quality_review":           "claude-sonnet-4-6",
    "report_generation":        "claude-sonnet-4-6",

    # Opus — complex chains, high-stakes exploitation, final judgment
    "exploitation_chain":       "claude-opus-4-7",
    "waf_bypass":               "claude-opus-4-7",
    "ssrf_rce_escalation":      "claude-opus-4-7",
    "injection_exploitation":   "claude-opus-4-7",
    "xss_exploitation":         "claude-opus-4-7",
    "auth_bypass_exploitation": "claude-opus-4-7",
    "final_judge":              "claude-opus-4-7",
    "counterfactual_analysis":  "claude-opus-4-7",
}

# Estimated tokens per turn by model (input + output combined)
TOKENS_PER_TURN: dict[str, int] = {
    "claude-haiku-4-5":  1_500,
    "claude-sonnet-4-6": 2_500,
    "claude-opus-4-7":   4_000,
}

# Base max_turns per role (before budget adjustment)
BASE_TURNS: dict[str, int] = {
    "scout":    60,
    "analyzer": 70,
    "exploiter": 75,
    "reporter": 40,
    "compressor": 5,
    "judge":    45,
}

# CVSS severity estimate by task type (0-10)
TASK_CVSS_WEIGHT: dict[str, float] = {
    "tool_output_parsing":      1.0,
    "coverage_tracking":        1.0,
    "context_compression":      1.0,
    "deduplication_check":      1.5,
    "phase_gate_validation":    2.0,
    "tool_output_compression":  1.0,
    "reconnaissance":           4.0,
    "info_gathering":           4.5,
    "configuration_testing":    5.0,
    "auth_testing":             7.5,
    "session_testing":          7.0,
    "vulnerability_analysis":   6.5,
    "canary_payload_testing":   6.0,
    "business_logic_testing":   6.0,
    "client_side_testing":      5.5,
    "error_handling_testing":   4.0,
    "crypto_testing":           5.5,
    "quality_review":           3.0,
    "report_generation":        2.5,
    "exploitation_chain":       9.0,
    "waf_bypass":               8.5,
    "ssrf_rce_escalation":      9.5,
    "injection_exploitation":   9.0,
    "xss_exploitation":         8.0,
    "auth_bypass_exploitation": 9.0,
    "final_judge":              3.5,
    "counterfactual_analysis":  7.0,
}

# Model pricing per million tokens (input/output average)
MODEL_COST_PER_MILLION: dict[str, float] = {
    "claude-haiku-4-5":  2.40,   # avg of $0.80 in / $4.00 out
    "claude-sonnet-4-6": 9.00,   # avg of $3.00 in / $15.00 out
    "claude-opus-4-7":   45.00,  # avg of $15.00 in / $75.00 out
}


class ModelRouter:
    def __init__(self, config_path: Optional[str] = None):
        self.task_map = TASK_MODEL_MAP.copy()
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                overrides = yaml.safe_load(f) or {}
            self.task_map.update(overrides.get("task_overrides", {}))

    def route(
        self,
        task_type: str,
        role: str,
        remaining_budget_usd: float,
        coverage_pct: float = 0.0,
        phase: int = 0,
    ) -> dict:
        ideal_model = self.task_map.get(task_type, "claude-sonnet-4-6")
        model = self._apply_budget_pressure(ideal_model, remaining_budget_usd)
        priority = self._score_priority(task_type, coverage_pct, phase)
        max_turns = self._calculate_turns(role, model, remaining_budget_usd, priority)
        estimated_cost = self._estimate_cost(model, max_turns)

        return {
            "model": model,
            "max_turns": max_turns,
            "priority_score": round(priority, 3),
            "estimated_cost_usd": round(estimated_cost, 4),
            "ideal_model": ideal_model,
            "downgraded": model != ideal_model,
        }

    def _apply_budget_pressure(self, ideal_model: str, remaining_usd: float) -> str:
        """Downgrade model tier when budget is tight."""
        if remaining_usd <= 0.50 and ideal_model == "claude-opus-4-7":
            return "claude-sonnet-4-6"
        if remaining_usd <= 0.20:
            return "claude-haiku-4-5"
        if remaining_usd <= 1.00 and ideal_model == "claude-opus-4-7":
            return "claude-sonnet-4-6"
        return ideal_model

    def _score_priority(self, task_type: str, coverage_pct: float, phase: int) -> float:
        cvss = TASK_CVSS_WEIGHT.get(task_type, 5.0)
        coverage_gap = max(0.0, 1.0 - coverage_pct)
        phase_urgency = 1.0 + (phase * 0.05)  # later phases slightly more urgent
        return (cvss * coverage_gap * phase_urgency) / 10.0

    def _calculate_turns(
        self,
        role: str,
        model: str,
        remaining_budget_usd: float,
        priority: float,
    ) -> int:
        base = BASE_TURNS.get(role, 50)
        cost_per_turn = (TOKENS_PER_TURN[model] * MODEL_COST_PER_MILLION[model]) / 1_000_000
        affordable_turns = int((remaining_budget_usd * min(priority, 1.0)) / cost_per_turn)
        return max(10, min(base, affordable_turns))

    def _estimate_cost(self, model: str, max_turns: int) -> float:
        tokens = TOKENS_PER_TURN[model] * max_turns
        return (tokens * MODEL_COST_PER_MILLION[model]) / 1_000_000
