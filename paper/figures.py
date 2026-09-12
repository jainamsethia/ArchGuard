"""Figs. 2 and 3 from measured ArchGuard data. Greyscale + hatch (print/CVD safe).

Fig. 1 is a schematic rather than a plot and is drawn by figure_pipeline.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "figures"

#: Printed width in inches. Figures are authored at the size they appear in
#: the paper so that a 7.5 pt label is 7.5 pt on the page.
COL_W = 2.45
OUT.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 7.5,
    "axes.linewidth": 0.6,
    "axes.edgecolor": "#333333",
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "savefig.dpi": 600,
    "figure.autolayout": False,
})

first = json.loads((ROOT / "results/first_scan.json").read_text(encoding="utf-8"))
rescan = json.loads((ROOT / "results/repeat_scan.json").read_text(encoding="utf-8"))
by = {r["repo"]: r for r in rescan}
order = [r["repo"] for r in first if r.get("status") == "ok"]

COL_A, COL_B = "#FFFFFF", "#9E9E9E"   # scan 1 / scan 2
EDGE = "#222222"


def fig_health() -> None:
    """Fig. 2 - health per repository, first vs repeat scan."""
    fig, ax = plt.subplots(figsize=(COL_W, COL_W * 2.15 / 3.42))
    h1 = [by[r]["health_1"] for r in order]
    h2 = [by[r]["health_2"] for r in order]
    x = range(len(order))
    bw = 0.40
    ax.bar([i - bw / 2 for i in x], h1, bw, label="First scan",
           facecolor=COL_A, edgecolor=EDGE, linewidth=0.6, hatch="////")
    ax.bar([i + bw / 2 for i in x], h2, bw, label="Repeat scan",
           facecolor=COL_B, edgecolor=EDGE, linewidth=0.6)
    ax.set_ylabel("Health score")
    ax.set_ylim(0, 118)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xticks(list(x))
    ax.set_xticklabels(order, rotation=38, ha="right", fontsize=6.2)
    ax.grid(axis="y", linewidth=0.4, color="#DDDDDD")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    # selective direct labels: only where the two scans differ
    for i, r in enumerate(order):
        if by[r]["health_1"] != by[r]["health_2"]:
            ax.text(i + bw / 2, by[r]["health_2"] + 2.0, f"{by[r]['health_2']:.0f}",
                    ha="center", fontsize=5.8)
    ax.legend(frameon=False, fontsize=6.3, loc="upper center", ncol=2,
              bbox_to_anchor=(0.5, 1.16), handlelength=1.5, columnspacing=1.4)
    fig.savefig(OUT / "fig2_health.png", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def fig_runtime() -> None:
    """Fig. 3 - wall-clock analysis time, first vs repeat scan."""
    fig, ax = plt.subplots(figsize=(COL_W, COL_W * 2.15 / 3.42))
    d1 = [by[r]["dur_1"] for r in order]
    d2 = [by[r]["dur_2"] for r in order]
    x = range(len(order))
    bw = 0.40
    ax.bar([i - bw / 2 for i in x], d1, bw, label="First scan",
           facecolor=COL_A, edgecolor=EDGE, linewidth=0.6, hatch="////")
    ax.bar([i + bw / 2 for i in x], d2, bw, label="Repeat scan",
           facecolor=COL_B, edgecolor=EDGE, linewidth=0.6)
    ax.set_ylabel("Analysis time (s)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(order, rotation=38, ha="right", fontsize=6.2)
    ax.grid(axis="y", linewidth=0.4, color="#DDDDDD")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.annotate("includes one-off\nmodel load", xy=(0, d1[0]), xytext=(1.35, d1[0] + 1.5),
                fontsize=5.6, ha="left", va="bottom",
                arrowprops={"arrowstyle": "-", "linewidth": 0.5, "color": "#555555"})
    ax.legend(frameon=False, fontsize=6.3, loc="upper right", handlelength=1.5)
    fig.savefig(OUT / "fig3_runtime.png", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


if __name__ == "__main__":
    fig_health()
    fig_runtime()
    for p in sorted(OUT.glob("*.png")):
        print(p.name, p.stat().st_size, "bytes")
