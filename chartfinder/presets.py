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
    #: 이 전략에 필요한 추가 데이터 ("flows" 수급 / "fundamentals" 재무)
    requires: list[str] = field(default_factory=list)
    #: 초보자용 화면에서 보여줄 순서 (작을수록 위)
    order: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "market": self.market,
            "universe": self.universe,
            "description": self.description,
            "requires": list(self.requires),
            "order": self.order,
            "conditions": [spec.to_dict() for spec in self.conditions],
        }

    @property
    def needs_flows(self) -> bool:
        return "flows" in self.requires

    @property
    def needs_fundamentals(self) -> bool:
        return "fundamentals" in self.requires


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
        requires=list(data.get("requires") or []),
        order=int(data.get("order", 100)),
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


def load_all(folder: str | Path = "presets") -> list[tuple[Path, Preset]]:
    """모든 프리셋을 order 순으로 (경로, 프리셋) 목록으로."""
    loaded = [(path, load(path)) for path in list_presets(folder)]
    return sorted(loaded, key=lambda item: (item[1].order, item[1].name))
