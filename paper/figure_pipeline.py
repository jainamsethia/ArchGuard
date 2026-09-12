"""Fig. 1 - stage pipeline, two rows, sized for a single IEEE column."""
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent / "figures"
OUT.mkdir(parents=True, exist_ok=True)

#: See figures.py -- authored at printed size.
COL_W = 2.45
EDGE = "#222222"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 7.5,
    "savefig.dpi": 600,
})

fig, ax = plt.subplots(figsize=(COL_W, COL_W * 2.25 / 3.42))
ax.set_xlim(0, 100)
ax.set_ylim(0, 68)
ax.axis("off")

w, h = 21.5, 15.0
row1 = [
    (2.0, 47.0, "Clone\n(pinned IP)"),
    (27.5, 47.0, "Parse\n(AST, imports)"),
    (53.0, 47.0, "Contract\ninference"),
    (76.5, 47.0, "Incremental\nplan"),
]
row2 = [
    (14.5, 15.0, "Four-layer\nanalysis L1-L4"),
    (40.0, 15.0, "Score\n(active mean)"),
    (65.5, 15.0, "Persist\n+ report"),
]


def box(x, y, label):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.25,rounding_size=0.8",
        linewidth=0.7, edgecolor=EDGE, facecolor="#F4F4F4"))
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
            fontsize=6.2, linespacing=1.35)


for x, y, lab in row1 + row2:
    box(x, y, lab)

for (x1, y1, _), (x2, _, _) in pairwise(row1):
    ax.add_patch(FancyArrowPatch((x1 + w + 0.2, y1 + h / 2), (x2 - 0.3, y1 + h / 2),
                                 arrowstyle="-|>", mutation_scale=6,
                                 linewidth=0.7, color=EDGE))
for (x1, y1, _), (x2, _, _) in pairwise(row2):
    ax.add_patch(FancyArrowPatch((x1 + w + 0.2, y1 + h / 2), (x2 - 0.3, y1 + h / 2),
                                 arrowstyle="-|>", mutation_scale=6,
                                 linewidth=0.7, color=EDGE))

# wrap from the end of row 1 down to the start of row 2, routed through the
# clear band between the rows so it crosses no box
x_from = row1[-1][0] + w / 2
x_to = row2[0][0] + w / 2
y_mid = 38.0
ax.plot([x_from, x_from], [46.7, y_mid], linewidth=0.7, color=EDGE, solid_capstyle="butt")
ax.plot([x_from, x_to], [y_mid, y_mid], linewidth=0.7, color=EDGE, solid_capstyle="butt")
ax.add_patch(FancyArrowPatch((x_to, y_mid), (x_to, 30.3), arrowstyle="-|>",
                             mutation_scale=6, linewidth=0.7, color=EDGE))

ax.text(53.0 + w / 2, 44.5, "git co-change history", ha="center", va="top",
        fontsize=5.5, style="italic")
ax.text(14.5 + w / 2, 12.5, "MiniLM + FAISS", ha="center", va="top",
        fontsize=5.5, style="italic")

fig.savefig(OUT / "fig1_pipeline.png", bbox_inches="tight", pad_inches=0.02)
plt.close(fig)
print("fig1_pipeline.png", (OUT / "fig1_pipeline.png").stat().st_size, "bytes")
