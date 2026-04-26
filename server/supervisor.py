"""
Token-aware supervisor — the central governor for all agent spawning decisions.
Consults the token ledger, scores task priority, and routes to the right model.
"""

import json
from pathlib import Path
from typing import Optional

from .token_ledger import TokenLedger
from .model_router import ModelRouter
from .tool_compressor import ToolCompressor


DEFAULT_BUDGET_USD = 15.00

# Thresholds for budget alerts
BUDGET_WARN_PCT   = 0.70   # warn at 70% spend
BUDGET_CRITICAL_PCT = 0.90  # downgrade all to Sonnet at 90%
BUDGET_EMERGENCY_PCT = 0.97  # downgrade all to Haiku at 97%


class Supervisor:
    def __init__(
        self,
        engagement_id: str,
        total_budget_usd: float = DEFAULT_BUDGET_USD,
        base_dir: str = "engagements",
        router_config: Optional[str] = None,
    ):
        self.eid = engagement_id
        self.total_budget = total_budget_usd
        self.ledger = TokenLedger(engagement_id, base_dir)
        self.router = ModelRouter(router_config)
        self._compressor: Optional[ToolCompressor] = None

    @property
    def compressor(self) -> ToolCompressor:
        if self._compressor is None:
            self._compressor = ToolCompressor()
        return self._compressor

    # ── Core routing ────────────────────────────────────────────────────────

    def route_task(
        self,
        task_type: str,
        role: str,
        phase: int = 0,
        coverage_pct: float = 0.0,
    ) -> dict:
        """
        Main entry point — returns model, max_turns, priority, estimated cost.
        Claude calls this before spawning every subagent.
        """
        spent = self.ledger.get_total_cost()
        remaining = self.total_budget - spent
        pct_used = spent / self.total_budget if self.total_budget else 1.0

        # Global budget override: emergency = everything goes Haiku
        if pct_used >= BUDGET_EMERGENCY_PCT:
            return self._emergency_routing(task_type, role, remaining)

        routing = self.router.route(
            task_type=task_type,
            role=role,
            remaining_budget_usd=remaining,
            coverage_pct=coverage_pct,
            phase=phase,
        )

        # Apply critical-budget pressure (force Sonnet ceiling)
        if pct_used >= BUDGET_CRITICAL_PCT and routing["model"] == "claude-opus-4-7":
            routing["model"] = "claude-sonnet-4-6"
            routing["downgraded"] = True
            routing["downgrade_reason"] = "budget_critical"

        task_id = self.ledger.log_task(
            task_type=task_type,
            model_assigned=routing["model"],
            priority_score=routing["priority_score"],
            max_turns=routing["max_turns"],
            estimated_cost=routing["estimated_cost_usd"],
        )

        routing["task_id"] = task_id
        routing["budget_pct_used"] = round(pct_used * 100, 1)
        routing["remaining_usd"] = round(remaining, 4)

        if pct_used >= BUDGET_WARN_PCT:
            routing["budget_warning"] = f"Budget {round(pct_used*100,1)}% used — consider scope reduction"

        return routing

    def _emergency_routing(self, task_type: str, role: str, remaining: float) -> dict:
        """All non-critical tasks collapse to Haiku when budget is nearly exhausted."""
        critical_tasks = {"exploitation_chain", "ssrf_rce_escalation", "injection_exploitation",
                          "final_judge", "xss_exploitation", "auth_bypass_exploitation"}
        model = "claude-sonnet-4-6" if task_type in critical_tasks else "claude-haiku-4-5"
        max_turns = 15
        return {
            "model": model,
            "max_turns": max_turns,
            "priority_score": 0.1,
            "estimated_cost_usd": 0.0,
            "ideal_model": model,
            "downgraded": True,
            "downgrade_reason": "budget_emergency",
            "budget_pct_used": 97.0,
            "remaining_usd": round(remaining, 4),
            "budget_warning": "EMERGENCY: budget nearly exhausted — minimal mode active",
        }

    # ── Token recording ─────────────────────────────────────────────────────

    def record_usage(
        self,
        agent_role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        phase: int = 0,
        task_type: str = "",
        cache_read: int = 0,
        cache_write: int = 0,
        task_id: Optional[int] = None,
        notes: str = "",
    ) -> dict:
        cost = self.ledger.record_usage(
            agent_role=agent_role,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            phase=phase,
            task_type=task_type,
            cache_read=cache_read,
            cache_write=cache_write,
            notes=notes,
        )
        if task_id:
            self.ledger.complete_task(task_id)

        return {
            "recorded": True,
            "cost_usd": cost,
            "total_spent_usd": round(self.ledger.get_total_cost(), 4),
        }

    # ── Budget status ────────────────────────────────────────────────────────

    def get_budget_status(self) -> dict:
        return self.ledger.get_status(self.total_budget)

    # ── Tool compression passthrough ─────────────────────────────────────────

    def compress_tool_output(self, tool_name: str, raw_output: str) -> dict:
        return self.compressor.compress(tool_name, raw_output)

    def compress_phase_summary(self, phase: int, raw_context: str) -> str:
        return self.compressor.compress_phase_context(phase, raw_context)

    def check_duplicate_finding(self, new_finding: str, existing_findings: list[str]) -> dict:
        return self.compressor.check_duplicate(new_finding, existing_findings)

    # ── Persistence ──────────────────────────────────────────────────────────

    def save_state(self) -> str:
        state_path = Path("engagements") / self.eid / "supervisor_state.json"
        state = {
            "engagement_id": self.eid,
            "total_budget_usd": self.total_budget,
            "budget_status": self.get_budget_status(),
        }
        state_path.write_text(json.dumps(state, indent=2))
        return str(state_path)

    @classmethod
    def load(cls, engagement_id: str, base_dir: str = "engagements") -> "Supervisor":
        state_path = Path(base_dir) / engagement_id / "supervisor_state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            budget = state.get("total_budget_usd", DEFAULT_BUDGET_USD)
        else:
            budget = DEFAULT_BUDGET_USD
        return cls(engagement_id=engagement_id, total_budget_usd=budget, base_dir=base_dir)
