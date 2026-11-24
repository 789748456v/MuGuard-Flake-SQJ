"""
Generate publication-grade vector architecture diagram for Section 4 (MuGuard-Flake).
Strictly contains ZERO hardcoded section/table/equation/algorithm numbers so it stays
invariant under any manuscript renumbering.
"""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon, FancyArrowPatch

FIG_DIR = Path(__file__).resolve().parent.parent / "paper" / "figures"


def draw_box(ax, x, y, w, h, title, body="", fc="#ffffff", ec="#2b4c7e", lw=1.3,
             title_color="#102a43", body_color="#243b53", title_size=9.4, body_size=8.1,
             bold_title=True, radius=0.14, zorder=3):
    box = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.04,rounding_size={radius}",
        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zorder
    )
    ax.add_patch(box)
    if body:
        ax.text(
            x + w / 2.0, y + h * 0.69, title,
            ha="center", va="center", fontsize=title_size,
            fontweight="bold" if bold_title else "normal",
            color=title_color, zorder=zorder + 1
        )
        ax.text(
            x + w / 2.0, y + h * 0.30, body,
            ha="center", va="center", fontsize=body_size,
            color=body_color, linespacing=1.22, zorder=zorder + 1
        )
    else:
        ax.text(
            x + w / 2.0, y + h / 2.0, title,
            ha="center", va="center", fontsize=title_size,
            fontweight="bold" if bold_title else "normal",
            color=title_color, linespacing=1.22, zorder=zorder + 1
        )


def draw_diamond(ax, cx, cy, hw, hh, label, fc="#fff8e6", ec="#b45309", lw=1.4, fontsize=8.6, zorder=3):
    pts = [(cx, cy + hh), (cx + hw, cy), (cx, cy - hh), (cx - hw, cy)]
    poly = Polygon(pts, closed=True, facecolor=fc, edgecolor=ec, linewidth=lw, zorder=zorder)
    ax.add_patch(poly)
    ax.text(cx, cy, label, ha="center", va="center", fontsize=fontsize,
            fontweight="bold", color="#78350f", linespacing=1.18, zorder=zorder + 1)


def arrow(ax, p1, p2, color="#334e68", lw=1.35, ls="-", rad=0.0, zorder=4):
    arr = FancyArrowPatch(
        p1, p2,
        arrowstyle="-|>,head_length=6.0,head_width=3.6",
        connectionstyle=f"arc3,rad={rad}",
        color=color, linewidth=lw, linestyle=ls, zorder=zorder
    )
    ax.add_patch(arr)


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "mathtext.default": "regular",
    })

    fig, ax = plt.subplots(figsize=(11.4, 6.85))
    ax.set_xlim(0, 11.4)
    ax.set_ylim(0, 6.85)
    ax.axis("off")

    # =========================================================================
    # BAND 1: OFFLINE ONE-TIME BASELINE PRE-COMPUTATION (y = 5.18 .. 6.70)
    # =========================================================================
    band1 = FancyBboxPatch(
        (0.14, 5.18), 11.12, 1.52,
        boxstyle="round,pad=0.02,rounding_size=0.14",
        facecolor="#f0f5f9", edgecolor="#9fb3c8", linewidth=1.1, linestyle="--", zorder=1
    )
    ax.add_patch(band1)
    ax.text(
        0.30, 6.50,
        r"Phase 1: Offline One-Time Baseline Pre-Computation (Fixed Production Code $P_0$)",
        ha="left", va="center", fontsize=9.6, fontweight="bold", color="#102a43", zorder=2
    )

    draw_box(ax, 0.30, 5.34, 2.25, 0.94,
             "Flaky Subject & Context",
             r"Target $T_{\mathrm{orig}}$ (+ Partner $T_{\mathrm{part}}$" + "\n" + r"if order-dependent) & $W^+$ trace",
             fc="#ffffff", ec="#486581")
    draw_box(ax, 3.00, 5.34, 2.35, 0.94,
             "Clean Reference & Scope",
             r"Verify 5/5 clean $e_0$ passes;" + "\n" + "trace dynamic focal lines/classes",
             fc="#ffffff", ec="#486581")
    draw_box(ax, 5.80, 5.34, 2.40, 0.94,
             "3-Run Kill Intersection",
             r"Attributed 3/3 kills under $e_0$" + "\n" + "(filters flaky/misattributed kills)",
             fc="#ffffff", ec="#486581")
    draw_box(ax, 8.65, 5.34, 2.45, 0.94,
             "Frozen Obligation Pools",
             r"Target $M_{\mathrm{det}}(T_{\mathrm{orig}})$ & Partner" + "\n" + r"$M_{\mathrm{det}}(T_{\mathrm{part}})$, role-tagged",
             fc="#e1effe", ec="#1f6f8b", title_color="#102a43")

    arrow(ax, (2.59, 5.81), (2.96, 5.81), color="#334e68")
    arrow(ax, (5.39, 5.81), (5.76, 5.81), color="#334e68")
    arrow(ax, (8.24, 5.81), (8.61, 5.81), color="#334e68")

    # =========================================================================
    # BAND 2: ONLINE ITERATIVE FAIL-CLOSED REPAIR LOOP (y = 1.54 .. 4.94)
    # =========================================================================
    band2 = FancyBboxPatch(
        (0.14, 1.54), 11.12, 3.40,
        boxstyle="round,pad=0.02,rounding_size=0.14",
        facecolor="#ffffff", edgecolor="#829ab1", linewidth=1.15, zorder=1
    )
    ax.add_patch(band2)
    ax.text(
        0.30, 4.74,
        r"Phase 2: Online Iterative Fail-Closed Repair Loop (Up to $K = 5$ Rounds)",
        ha="left", va="center", fontsize=9.6, fontweight="bold", color="#102a43", zorder=2
    )

    # Row A inside Band 2 (y = 3.22 .. 4.16) — plenty of headroom below Phase 2 title for short-circuit arrow!
    draw_box(ax, 0.30, 3.22, 2.08, 0.94,
             "1. LLM Candidate Patch",
             r"Generate $T'_r$ from code," + "\n" + r"$W^+$ trace & prior feedback",
             fc="#f8fafc", ec="#1f6f8b")
    draw_box(ax, 2.76, 3.22, 2.34, 0.94,
             "2. Sanity & Scope Diff",
             "Reject skips/swallows;\ntag target-only vs. shared-scope",
             fc="#f8fafc", ec="#1f6f8b")
    draw_box(ax, 5.48, 3.22, 2.40, 0.94,
             "3. Short-Circuit Validator",
             r"Compile & run stability $W^+$" + "\n" + r"(fail $\rightarrow$ return trace immediately)",
             fc="#fef3c7", ec="#d97706", title_color="#78350f")
    draw_box(ax, 8.26, 3.22, 2.84, 0.94,
             "4. Dual-Role Mutation Gate",
             r"Probe $M_{\mathrm{det}}(T_{\mathrm{orig}})$ under $e_{\mathrm{iso}}$;" + "\n" + r"probe $M_{\mathrm{det}}(T_{\mathrm{part}})$ if shared-scope",
             fc="#e1effe", ec="#1f6f8b")

    arrow(ax, (2.42, 3.69), (2.72, 3.69), color="#1f6f8b")
    arrow(ax, (5.14, 3.69), (5.44, 3.69), color="#1f6f8b")
    arrow(ax, (7.92, 3.69), (8.22, 3.69), color="#1f6f8b")

    # Vertical feed from Frozen Obligation Pools -> Dual-Role Mutation Gate
    arrow(ax, (9.82, 5.30), (9.82, 4.20), color="#1f6f8b", lw=1.45, ls="--")
    ax.text(9.92, 5.05, "Read frozen pools", ha="left", va="center", fontsize=7.9, style="italic", color="#1f6f8b", zorder=5)

    # Short-circuit retry loop cleanly BETWEEN title (y=4.74) and Row A top (y=4.20)
    ax.plot([6.68, 6.68, 1.34, 1.34], [4.20, 4.44, 4.44, 4.24], color="#b45309", lw=1.25, ls=":", zorder=4)
    arrow(ax, (1.34, 4.38), (1.34, 4.20), color="#b45309", lw=1.25)
    ax.text(4.01, 4.48, r"Compile / $W^+$ fail (short-circuit before mutation probing)",
            ha="center", va="bottom", fontsize=7.8, color="#92400e", zorder=5)

    # Row B inside Band 2 (y = 1.72 .. 2.72) — ZERO crossing arrows!
    # Left (directly under LLM Candidate Patch): Differential Witness Synthesis
    draw_box(ax, 0.30, 1.84, 2.66, 0.88,
             "Differential Witness Synthesis",
             r"Select $\leq 4$ surviving mutants +" + "\n" + r"$\pm 3$-line $P_0$ slices ($r < K$)",
             fc="#fef3c7", ec="#d9822b", lw=1.4, title_color="#78350f")

    # Clean straight vertical arrow UP from Witness Synthesis -> LLM Candidate Patch!
    arrow(ax, (1.34, 2.76), (1.34, 3.18), color="#d9822b", lw=1.5)
    ax.text(1.44, 2.97, r"Witness retry $\to$ round $r+1$", ha="left", va="center",
            fontsize=7.8, fontweight="bold", color="#92400e", zorder=5)

    # Middle-Left: EXPLICIT ABSTENTION (cleanly receives bottom branch of diamond with zero crossing!)
    draw_box(ax, 3.38, 1.72, 2.52, 0.88,
             "EXPLICIT ABSTENTION",
             r"$r = K$, uncovered partner, or probe" + "\n" + r"error $\rightarrow$ attach witnesses for review",
             fc="#fde8e8", ec="#b22222", lw=1.45, title_color="#7f1d1d", body_color="#7f1d1d")

    # Right-Center: Fail-Closed Decision Diamond
    draw_diamond(ax, 7.18, 2.28, 0.96, 0.48, "Fail-Closed\nVerdict?", fc="#fff8e6", ec="#b45309", fontsize=8.4)

    # Arrow from Dual-Role Mutation Gate straight down & left into top of Diamond
    ax.plot([9.68, 9.68, 7.18], [3.18, 2.95, 2.95], color="#1f6f8b", lw=1.35, zorder=4)
    arrow(ax, (7.18, 2.95), (7.18, 2.78), color="#1f6f8b", lw=1.35)

    # Right: SAFE_PASS (generous gap from diamond!)
    draw_box(ax, 8.76, 1.84, 2.34, 0.86,
             "SAFE_PASS (Auto-Merge)",
             r"$M_{\mathrm{surv}} = \emptyset$, 0 unknowns," + "\n" + "partner covered",
             fc="#def7ec", ec="#2a7f62", lw=1.5, title_color="#03543f", body_color="#046c4e")
    arrow(ax, (8.16, 2.28), (8.72, 2.28), color="#2a7f62", lw=1.5)
    ax.text(8.44, 2.37, "Clean", ha="center", va="bottom", fontsize=7.9, fontweight="bold", color="#03543f")

    # Witness feedback route: from LEFT tip of diamond -> cleanly ABOVE Abstention box -> into RIGHT side of Witness box!
    ax.plot([6.20, 6.20, 3.16, 3.16], [2.28, 2.86, 2.86, 2.28], color="#d9822b", lw=1.4, zorder=4)
    arrow(ax, (3.16, 2.28), (3.00, 2.28), color="#d9822b", lw=1.4)
    ax.text(4.68, 2.90, r"$M_{\mathrm{surv}} \neq \emptyset$ & $r < K$ (erosion retry)",
            ha="center", va="bottom", fontsize=7.8, fontweight="bold", color="#92400e", zorder=5)

    # Abstention route: from BOTTOM tip of diamond -> cleanly into RIGHT side of Abstention box!
    ax.plot([7.18, 7.18, 6.08], [1.78, 1.66, 1.66], color="#b22222", lw=1.3, ls="--", zorder=4)
    ax.plot([6.08, 6.08], [1.66, 2.02], color="#b22222", lw=1.3, ls="--", zorder=4)
    arrow(ax, (6.08, 2.02), (5.94, 2.02), color="#b22222", lw=1.3)
    ax.text(7.24, 1.63, "Exhausted / uncovered / error",
            ha="left", va="center", fontsize=7.5, color="#7f1d1d", zorder=5)

    # =========================================================================
    # BAND 3: RUNNING EXAMPLE TRACE (ToStringBuilderTest.testReflectionStatics)
    # =========================================================================
    band3 = FancyBboxPatch(
        (0.14, 0.12), 11.12, 1.24,
        boxstyle="round,pad=0.02,rounding_size=0.14",
        facecolor="#f8f9fa", edgecolor="#adb5bd", linewidth=1.1, zorder=1
    )
    ax.add_patch(band3)
    ax.text(
        0.30, 1.19,
        r"Running Example: Apache Commons Lang (ToStringBuilderTest.testReflectionStatics, $|M_{\mathrm{det}}| = 92$)",
        ha="left", va="center", fontsize=9.1, fontweight="bold", color="#102a43", zorder=2
    )

    draw_box(ax, 0.30, 0.24, 3.18, 0.78,
             "Round 1: Stabilized but Eroded",
             'fieldBody.split(",") passes NonDex ' + r"$W^+$" + "\nBlocked: 84/92 killed (8 faults survive)",
             fc="#fff5f5", ec="#b22222", lw=1.1, title_size=8.6, body_size=7.7, title_color="#7f1d1d")
    draw_box(ax, 3.96, 0.24, 3.60, 0.78,
             "Localized Differential Witness",
             "[target] ToStringStyle.appendEnd:L427\nRemoved call: removeLastFieldSeparator(buffer)",
             fc="#fffbeb", ec="#d97706", lw=1.1, title_size=8.6, body_size=7.7, title_color="#78350f")
    draw_box(ax, 8.04, 0.24, 3.06, 0.78,
             "Round 2: Witness-Guided SAFE_PASS",
             'fieldBody.split(",", -1) keeps trailing\nempty tokens ' + r"$\rightarrow$" + " 92/92 killed (100% retained)",
             fc="#def7ec", ec="#2a7f62", lw=1.1, title_size=8.6, body_size=7.7, title_color="#03543f")

    arrow(ax, (3.52, 0.63), (3.92, 0.63), color="#b45309", lw=1.35)
    arrow(ax, (7.60, 0.63), (8.00, 0.63), color="#2a7f62", lw=1.35)

    plt.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    for ext in ("pdf", "png"):
        fig.savefig(FIG_DIR / f"fig0_muguard_architecture.{ext}", dpi=300, bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    print(f"[OK] Regenerated clean {FIG_DIR / 'fig0_muguard_architecture.pdf'} and .png")


if __name__ == "__main__":
    main()
