"""조건 세트(프리셋) 저장/불러오기 — YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .screener import ConditionSpec


@dataclass
class Preset:
    name: str
    market: str = "kr"
    universe: str = "all"
    conditions: list[ConditionSpec] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "market": self.market,
            "universe": self.universe,
            "description": self.description,
            "conditions": [spec.to_dict() for spec in self.conditions],
        }


def load(path: str | Path) -> Preset:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw_conditions = data.get("conditions") or []
    specs = []
    for item in raw_conditions:
        if isinstance(item, str):
            specs.append(ConditionSpec.parse(item))
        else:
            specs.append(
                ConditionSpec(
                    key=item["key"],
                    params=item.get("params") or {},
                    weight=float(item.get("weight", 1.0)),
                )
            )
    return Preset(
        name=data.get("name") or Path(path).stem,
        market=data.get("market", "kr"),
        universe=data.get("universe", "all"),
        conditions=specs,
        description=data.get("description", ""),
    )


def save(preset: Preset, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(preset.to_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def list_presets(folder: str | Path = "presets") -> list[Path]:
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob("*.yaml"))
