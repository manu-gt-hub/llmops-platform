"""
budgets.py — Monthly spend budgets per project (config/budgets.yaml).
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Budget:
    project: str
    monthly_usd: float
    alert_at: float


class Budgets:
    def __init__(self, default: Budget, projects: dict[str, Budget]):
        self.default = default
        self.projects = projects

    @classmethod
    def load(cls, path: Path) -> "Budgets":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        d = data.get("default") or {}
        default = Budget("default", float(d.get("monthly_usd", 20)), float(d.get("alert_at", 0.8)))
        projects = {
            name: Budget(
                name,
                float(cfg.get("monthly_usd", default.monthly_usd)),
                float(cfg.get("alert_at", default.alert_at)),
            )
            for name, cfg in (data.get("projects") or {}).items()
        }
        return cls(default, projects)

    def for_project(self, project: str) -> Budget:
        if project in self.projects:
            return self.projects[project]
        return Budget(project, self.default.monthly_usd, self.default.alert_at)

    def all(self) -> list[Budget]:
        return [self.default, *self.projects.values()]


def over_budget(spent_usd: float, budget: Budget) -> bool:
    return budget.monthly_usd > 0 and spent_usd >= budget.monthly_usd
