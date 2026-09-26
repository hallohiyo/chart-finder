"""조건 세트(프리셋) 저장/불러오기 — YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .screener import ConditionSpec


def default_dir() -> Path:
    """프리셋 폴더.

    바탕화면 바로가기처럼 현재 폴더가 다른 곳에서 실행돼도 찾을 수 있도록
    설치된 위치를 먼저 본다. 거기 없으면 현재 폴더의 presets 를 쓴다.
    """
    packaged = Path(__file__).resolve().parents[1] / "presets"
    if packaged.is_dir():
        return packaged
    return Path("presets")


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

    @property
    def needs_profiles(self) -> bool:
        return "profiles" in self.requires


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


def list_presets(folder: str | Path | None = None) -> list[Path]:
    folder = Path(folder) if folder is not None else default_dir()
    if not folder.exists():
        return []
    return sorted(p for p in folder.glob("*.yaml"))


def merge(presets: Iterable[Preset]) -> list[ConditionSpec]:
    """여러 전략의 조건을 하나로 합친다.

    같은 조건이 여러 전략에 들어 있으면 가중치를 더한다 (여러 전략이 동시에
    중요하다고 본 조건이므로). 파라미터는 먼저 나온 전략의 것을 쓴다 —
    스크리너가 조건을 키로 구분하므로 같은 키를 둘로 둘 수 없다.
    """
    merged: dict[str, ConditionSpec] = {}
    for preset in presets:
        for spec in preset.conditions:
            existing = merged.get(spec.key)
            if existing is None:
                merged[spec.key] = ConditionSpec(spec.key, dict(spec.params), spec.weight)
            else:
                existing.weight += spec.weight
    return list(merged.values())


def indicator_tags(preset: Preset) -> list[str]:
    """전략이 쓰는 지표 갈래 (화면에 '이동평균 · 거래량' 처럼 보여주려고)."""
    from .conditions import get as get_condition

    seen: list[str] = []
    for spec in preset.conditions:
        category = get_condition(spec.key).category
        if category not in seen:
            seen.append(category)
    return seen


def load_all(folder: str | Path | None = None) -> list[tuple[Path, Preset]]:
    """모든 프리셋을 order 순으로 (경로, 프리셋) 목록으로."""
    loaded = [(path, load(path)) for path in list_presets(folder)]
    return sorted(loaded, key=lambda item: (item[1].order, item[1].name))
