"""tkinter 데스크톱 UI — 설치 없이 바로 뜨는 간단한 창.

실행: python -m chartfinder.ui   (또는 chartfinder-ui)
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

# 패키지를 설치하지 않고 파일을 직접 실행해도 돌아가도록 저장소 루트를 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chartfinder import cache, presets as presets_mod
from chartfinder.conditions import by_category, get as get_condition
from chartfinder.datasource import MARKETS, universes
from chartfinder.screener import ConditionSpec, screen

MARKET_LABELS = {"kr": "한국", "us": "미국", "demo": "데모(오프라인)"}
PRESET_DIR = Path("presets")
BASE_COLUMNS = ("순위", "종목", "이름", "점수", "충족", "종가", "등락%")


class ScrollFrame(ttk.Frame):
    """세로 스크롤이 되는 프레임. 내용은 `.body` 안에 넣는다."""

    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas)

        window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.body.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(window, width=e.width))
        # 휠 스크롤 (윈도우/맥/리눅스)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-e.delta / (120 if e.delta % 120 == 0 else 1)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))


class ConditionRow:
    """조건 하나 = 체크박스 + (체크했을 때만 펼쳐지는) 파라미터 입력칸."""

    def __init__(self, parent: ttk.Frame, cond, on_toggle: Callable[[], None]) -> None:
        self.cond = cond
        self.checked = tk.BooleanVar(value=False)
        self.weight = tk.StringVar(value="1")
        self.params: dict[str, tk.StringVar] = {}
        self.on_toggle = on_toggle

        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="x", padx=4, pady=1)

        ttk.Checkbutton(
            self.frame, text=cond.label, variable=self.checked, command=self._toggle
        ).pack(anchor="w")

        # 파라미터 줄은 체크하기 전까지 숨겨둔다 (목록이 길어지지 않게)
        self.fields = ttk.Frame(self.frame)
        for param in cond.params:
            var = tk.StringVar(value=str(param.default))
            self.params[param.name] = var
            ttk.Label(self.fields, text=param.label, foreground="#555").pack(side="left", padx=(0, 3))
            ttk.Entry(self.fields, textvariable=var, width=7).pack(side="left", padx=(0, 8))
        ttk.Label(self.fields, text="가중치", foreground="#555").pack(side="left", padx=(0, 3))
        ttk.Entry(self.fields, textvariable=self.weight, width=4).pack(side="left")

    def _toggle(self) -> None:
        if self.checked.get():
            self.fields.pack(anchor="w", padx=(22, 0), pady=(0, 3))
        else:
            self.fields.pack_forget()
        self.on_toggle()

    def to_spec(self) -> ConditionSpec | None:
        if not self.checked.get():
            return None
        return ConditionSpec(
            key=self.cond.key,
            params={name: var.get() for name, var in self.params.items()},
            weight=float(self.weight.get() or 1),
        )

    def apply(self, params: dict, weight: float) -> None:
        """프리셋 값을 입력칸에 반영."""
        self.checked.set(True)
        self.weight.set(f"{weight:g}")
        for name, value in params.items():
            if name in self.params:
                self.params[name].set(str(value))
        self._toggle()

    def clear(self) -> None:
        self.checked.set(False)
        self.fields.pack_forget()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("차트 조건 검색기")
        self.geometry("1180x760")
        self.minsize(900, 600)

        self.result = None
        self.result_market = "kr"
        self.queue: queue.Queue = queue.Queue()
        self.busy = False

        self._build_toolbar()
        self._build_body()
        self._build_status()
        self._refresh_universes()
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ 화면 구성
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="시장").pack(side="left")
        # 콤보박스에는 한글 라벨을 보여주고, self.market 은 시장 코드를 들고 있는다
        self.market = tk.StringVar(value="kr")
        self.market_box = ttk.Combobox(
            bar, width=14, state="readonly", values=[MARKET_LABELS[m] for m in MARKETS],
        )
        self.market_box.set(MARKET_LABELS["kr"])
        self.market_box.pack(side="left", padx=(4, 12))
        self.market_box.bind(
            "<<ComboboxSelected>>", lambda e: self._on_market_change(self.market_box.get())
        )

        ttk.Label(bar, text="유니버스").pack(side="left")
        self.universe = tk.StringVar()
        self.universe_box = ttk.Combobox(bar, textvariable=self.universe, width=10, state="readonly")
        self.universe_box.pack(side="left", padx=(4, 12))

        ttk.Label(bar, text="상위").pack(side="left")
        self.top = tk.StringVar(value="30")
        ttk.Spinbox(bar, from_=5, to=500, increment=5, textvariable=self.top, width=5).pack(
            side="left", padx=(4, 12)
        )

        self.strict = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="엄격 모드", variable=self.strict).pack(side="left", padx=(0, 12))

        self.flows = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="수급 포함", variable=self.flows).pack(side="left", padx=(0, 8))

        ttk.Button(bar, text="데이터 받기", command=self.on_update).pack(side="left")
        ttk.Button(bar, text="검색", command=self.on_scan).pack(side="left", padx=4)
        ttk.Button(bar, text="CSV 저장", command=self.on_save_csv).pack(side="left")

        self.preset = tk.StringVar()
        presets = [p.name for p in presets_mod.list_presets(PRESET_DIR)]
        if presets:
            ttk.Label(bar, text="프리셋").pack(side="left", padx=(16, 4))
            box = ttk.Combobox(bar, textvariable=self.preset, values=presets, width=22, state="readonly")
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", lambda e: self.on_load_preset())

    def _build_body(self) -> None:
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.LabelFrame(pane, text="조건 선택", padding=4)
        scroll = ScrollFrame(left)
        scroll.pack(fill="both", expand=True)
        left.configure(width=420)
        pane.add(left, weight=2)

        self.rows: list[ConditionRow] = []
        for category, conds in by_category().items():
            group = ttk.LabelFrame(scroll.body, text=category, padding=2)
            group.pack(fill="x", padx=2, pady=3)
            for cond in conds:
                self.rows.append(ConditionRow(group, cond, self._update_selected_label))

        right = ttk.LabelFrame(pane, text="결과", padding=4)
        self.tree = ttk.Treeview(right, columns=BASE_COLUMNS, show="headings", height=25)
        yscroll = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self._set_columns(BASE_COLUMNS)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_open_chart)
        pane.add(right, weight=5)

    def _build_status(self) -> None:
        bar = ttk.Frame(self, padding=(8, 0, 8, 8))
        bar.pack(fill="x")
        self.status = tk.StringVar(value="조건을 선택하고 검색을 누르세요. (행을 더블클릭하면 차트가 열립니다)")
        ttk.Label(bar, textvariable=self.status, foreground="#444").pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=220)
        self.progress.pack(side="right")

    def _set_columns(self, columns: tuple[str, ...]) -> None:
        self.tree.configure(columns=columns)
        widths = {"순위": 45, "종목": 95, "이름": 200, "점수": 60, "충족": 55, "종가": 110, "등락%": 62}
        for col in columns:
            self.tree.heading(col, text=col)
            # 조건 점수 열은 한글 라벨이 잘리지 않을 만큼 넓힌다
            width = widths.get(col, max(80, len(col) * 15 + 16))
            self.tree.column(col, width=width, minwidth=45,
                             anchor="w" if col == "이름" else "center")

    # ------------------------------------------------------------------ 상태
    def _on_market_change(self, label: str) -> None:
        for code, text in MARKET_LABELS.items():
            if text == label:
                self.market.set(code)
                break
        self._refresh_universes()

    def _refresh_universes(self) -> None:
        options = list(universes(self.market.get()))
        self.universe_box.configure(values=options)
        self.universe.set(options[0])

    def _update_selected_label(self) -> None:
        count = sum(1 for row in self.rows if row.checked.get())
        self.status.set(f"조건 {count}개 선택됨" if count else "조건을 선택하세요.")

    def selected_specs(self) -> list[ConditionSpec]:
        specs = []
        for row in self.rows:
            spec = row.to_spec()
            if spec is not None:
                specs.append(spec)
        return specs

    # ------------------------------------------------------------------ 백그라운드 작업
    def run_worker(self, fn: Callable[[], object], done: Callable[[object], None]) -> None:
        """무거운 작업을 별도 스레드에서 돌려 창이 멈추지 않게 한다."""
        if self.busy:
            messagebox.showinfo("진행 중", "이전 작업이 끝날 때까지 기다려 주세요.")
            return
        self.busy = True
        self.progress["value"] = 0

        def target() -> None:
            try:
                result = fn()
                self.queue.put(("done", result, done))
            except Exception as exc:  # 사용자에게 그대로 보여준다
                self.queue.put(("error", exc, done))

        threading.Thread(target=target, daemon=True).start()

    def _drain_queue(self) -> None:
        while not self.queue.empty():
            kind, payload, done = self.queue.get()
            if kind == "progress":
                current, total, text = payload
                self.progress["maximum"] = max(total, 1)
                self.progress["value"] = current
                self.status.set(text)
            elif kind == "done":
                self.busy = False
                done(payload)
            elif kind == "error":
                self.busy = False
                self.status.set("오류")
                messagebox.showerror("오류", f"{type(payload).__name__}: {payload}")
        self.after(100, self._drain_queue)

    def report(self, current: int, total: int, text: str) -> None:
        self.queue.put(("progress", (current, total, text), None))

    # ------------------------------------------------------------------ 동작
    def on_update(self) -> None:
        market, universe, flows = self.market.get(), self.universe.get(), self.flows.get()

        def work():
            return cache.update(
                market, universe, flows=flows,
                progress=lambda done, total, sym: self.report(done, total, f"수집 중 {done}/{total} · {sym}"),
            )

        def finish(stats):
            self.progress["value"] = self.progress["maximum"]
            text = (f"수집 완료 · 갱신 {stats['updated']} · 최신 {stats['skipped']}"
                    f" · 실패 {stats['failed']}")
            if flows:
                text += f" · 수급 {stats['flows']}"
                if stats.get("flow_error"):
                    messagebox.showwarning("수급 수집 실패", str(stats["flow_error"]))
            self.status.set(text)

        self.status.set("종목 목록을 받는 중…")
        self.run_worker(work, finish)

    def on_scan(self) -> None:
        specs = self.selected_specs()
        if not specs:
            messagebox.showwarning("조건 없음", "조건을 하나 이상 선택하세요.")
            return
        market, universe = self.market.get(), self.universe.get()
        top, strict = int(self.top.get() or 30), self.strict.get()

        def work():
            tickers = cache.get_tickers(market, universe)
            return screen(
                market, specs, tickers=tickers, strict=strict, top=top,
                progress=lambda done, total: self.report(done, total, f"채점 중 {done}/{total}"),
            )

        def finish(result):
            self.result, self.result_market = result, market
            self._show_result(result, specs)

        self.status.set("검색 중…")
        self.run_worker(work, finish)

    def _show_result(self, result, specs: list[ConditionSpec]) -> None:
        self.tree.delete(*self.tree.get_children())
        score_cols = [f"s_{spec.key}" for spec in specs]
        labels = [get_condition(spec.key).label for spec in specs]
        self._set_columns(BASE_COLUMNS + tuple(labels))

        if result is None or result.empty:
            self.status.set("조건에 맞는 종목이 없습니다. 데이터를 먼저 받으셨나요?")
            return

        for i, row in enumerate(result.itertuples(index=False), start=1):
            values = [
                i, row.symbol, row.name, f"{row.score:.3f}",
                f"{row.matched}/{len(specs)}", f"{row.close:,.2f}", f"{row.chg_pct:+.2f}",
            ]
            values += [f"{getattr(row, col):.2f}" for col in score_cols]
            self.tree.insert("", "end", values=values)
        self.progress["value"] = self.progress["maximum"]
        self.status.set(f"{len(result)}종목 · 행을 더블클릭하면 차트가 열립니다")

    def on_open_chart(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        values = self.tree.item(selection[0], "values")
        symbol, name = values[1], values[2]
        df = cache.load(self.result_market, symbol)
        if df is None or df.empty:
            messagebox.showwarning("데이터 없음", f"{symbol} 의 시세 캐시가 없습니다.")
            return
        try:
            from chartfinder.charts import open_in_browser

            open_in_browser(df, f"{symbol} {name}")
            self.status.set(f"{symbol} 차트를 브라우저에서 열었습니다.")
        except ImportError:
            messagebox.showinfo(
                "plotly 필요",
                "차트를 보려면 plotly 가 필요합니다.\n\n    pip install plotly",
            )

    def on_save_csv(self) -> None:
        if self.result is None or self.result.empty:
            messagebox.showwarning("결과 없음", "먼저 검색을 실행하세요.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"scan_{self.result_market}.csv",
        )
        if path:
            self.result.to_csv(path, index=False, encoding="utf-8-sig")
            self.status.set(f"저장됨: {path}")

    def on_load_preset(self) -> None:
        name = self.preset.get()
        if not name:
            return
        preset = presets_mod.load(PRESET_DIR / name)
        by_key = {row.cond.key: row for row in self.rows}
        for row in self.rows:
            row.clear()
        for spec in preset.conditions:
            row = by_key.get(spec.key)
            if row:
                row.apply(get_condition(spec.key).resolve(spec.params), spec.weight)
        if preset.market in MARKET_LABELS:
            self.market.set(preset.market)
            self.market_box.set(MARKET_LABELS[preset.market])
        self._refresh_universes()
        if preset.universe in universes(preset.market):
            self.universe.set(preset.universe)
        self._update_selected_label()
        self.status.set(f"프리셋 '{preset.name}' 을(를) 불러왔습니다.")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
