"""python -m eval.generate [--seed 2026] [--out eval/data]

Writes eval/data/<split>/<id>.(xml|pdf), <id>.truth.json and manifest.json (processing
order). The generated files are committed, so the LLM and KoSIT caches stay valid.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from facturx import generate_from_binary

from .dataset import Invoice, generate_split
from .render_pdf import render_pdf
from .render_xml import cii, ubl

DEFAULT_SEED = 2026
SPLITS = ("dev", "test")


def file_content(inv: Invoice) -> tuple[str, bytes]:
    if inv.kind == "xml_ubl":
        return "xml", ubl(inv)
    if inv.kind == "xml_cii":
        return "xml", cii(inv)
    if inv.kind == "zugferd":
        visual = render_pdf(inv)
        return "pdf", generate_from_binary(visual, cii(inv), flavor="factur-x", check_xsd=False)
    return "pdf", render_pdf(inv)


def write_split(out: Path, split: str, seed: int) -> list[dict]:
    folder = out / split
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    manifest = []
    for inv in generate_split(split, seed):
        ext, content = file_content(inv)
        name = f"{inv.id}.{ext}"
        (folder / name).write_bytes(content)
        entry = {
            "id": inv.id,
            "file": name,
            "kind": inv.kind,
            "layout": inv.layout if inv.kind in ("plain_pdf", "zugferd") else None,
            "planted": inv.planted,
            "duplicate_of": inv.duplicate_of,
        }
        truth = {**entry, "truth": inv.truth().model_dump(mode="json")}
        (folder / f"{inv.id}.truth.json").write_text(
            json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest.append(entry)
    (folder / "manifest.json").write_text(
        json.dumps({"split": split, "seed": seed, "invoices": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("eval/data"))
    args = parser.parse_args()
    for split in SPLITS:
        manifest = write_split(args.out, split, args.seed)
        planted = sum(1 for m in manifest if m["planted"])
        kinds = {}
        for m in manifest:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        print(f"{split}: {len(manifest)} invoices {kinds}, {planted} with a planted problem")


if __name__ == "__main__":
    main()
