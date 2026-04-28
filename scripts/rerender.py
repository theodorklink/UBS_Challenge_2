"""
Re-render the Bloomberg HTML view from existing JSON outputs (no API calls).
Used after fixing rendering bugs without needing to re-run the full pipeline.

Usage:
    python scripts/rerender.py          # re-render the latest run
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.comparables.models import CompTable  # noqa: E402
from src.spread.cross_basket import SpreadAnalysis  # noqa: E402
from src.summary.commentary import Commentary  # noqa: E402
from src.render.bloomberg_view import render_pair_view  # noqa: E402


def latest(pattern: str, root: Path) -> Path | None:
    files = sorted(root.glob(pattern))
    return files[-1] if files else None


def main() -> int:
    out = ROOT / "outputs"
    china_path = latest("china_td_002028.SZ_*.json", out / "comp_tables")
    global_path = latest("global_power_ENR.DE_*.json", out / "comp_tables")
    spread_path = latest("sieyuan_vs_siemens_*.json", out / "spread_analysis")
    commentary_path = latest("commentary_*.json", out / "valuation_drafts")

    if not (china_path and global_path and spread_path):
        print("Missing JSON outputs — run `python -m src.cli run` first.")
        return 1

    print(f"China table:  {china_path.name}")
    print(f"Global table: {global_path.name}")
    print(f"Spread:       {spread_path.name}")
    print(f"Commentary:   {commentary_path.name if commentary_path else '(none)'}")

    china_table = CompTable.model_validate_json(china_path.read_text())
    global_table = CompTable.model_validate_json(global_path.read_text())
    spread = SpreadAnalysis.model_validate_json(spread_path.read_text())
    commentary = Commentary.model_validate_json(commentary_path.read_text()) if commentary_path else None

    timestamp = china_path.stem.split("_")[-2] + "_" + china_path.stem.split("_")[-1]
    out_html = out / "views" / f"sieyuan_vs_siemens_{timestamp}.html"
    render_pair_view(china_table, global_table, spread, commentary, out_html)

    # Mirror to public/index.html for Vercel.
    public_html = ROOT / "public" / "index.html"
    public_html.parent.mkdir(parents=True, exist_ok=True)
    public_html.write_bytes(out_html.read_bytes())

    print(f"\nRe-rendered: {out_html}")
    print(f"Public copy: {public_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
