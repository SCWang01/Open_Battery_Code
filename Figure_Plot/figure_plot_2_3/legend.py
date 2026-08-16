from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


COLORS = {
    "MC": "#C90000",
    "CC": "#FF6B6B",
    "CD": "#F6B4B4",
    "NU": "#A6A6A6",
    "DC": "#9CC5E5",
    "DD": "#4F95D5",
    "MD": "#1D4B73",
}

LEGEND_LABELS = {
    "MC": "Max-rate charge",
    "CC": "Charge-for-charge",
    "CD": "Charge-for-discharge",
    "NU": "Null segment",
    "DC": "Discharge-for-charge",
    "DD": "Discharge-for-discharge",
    "MD": "Max-rate discharge",
}


# Match the order shown in the reference figure: discharge items first,
# followed by charge items and the null stair.
LEGEND_ORDER = ("MD", "DD", "DC", "MC", "CC", "CD", "NU")


def export_legend(output_path="Figs/legend.png"):
    """Export the horizontal legend strip as a transparent PNG."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plt.rcParams["font.family"] = "Times New Roman"
    plt.rcParams["font.size"] = 14

    handles = [
        Line2D(
            [0],
            [0],
            color=COLORS[key],
            lw=3,
            label=LEGEND_LABELS[key],
        )
        for key in LEGEND_ORDER
    ]

    figure, axis = plt.subplots(figsize=(14, 0.7))
    axis.axis("off")
    axis.legend(
        handles=handles,
        loc="center",
        ncol=len(handles),
        frameon=False,
        handlelength=1,
        handletextpad=0.2,
        columnspacing=0.6,
        borderaxespad=0,
        prop={"family": "Times New Roman", "size": 14, "weight": "bold"},
    )
    figure.savefig(output_path, dpi=600, bbox_inches="tight", pad_inches=0.03, transparent=True)
    plt.close(figure)
    return output_path


if __name__ == "__main__":
    print(f"Exported legend to {export_legend()}")
