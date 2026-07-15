"""Persist the transparent, manually reviewed provisional pilot labels.

These labels are an agent-assisted pilot review, not a substitute for the
independent human labels required by formal acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "evaluation" / "pilot" / "artifacts"
ANNOTATOR = "codex_provisional_manual_review"
TIMESTAMP = "2026-07-13T00:00:00+00:00"

LABELS = {
    "ds_20260707_221719_ai": {
        "included": {"evidence_3e9ed7d29a9a0b1ff68360e4", "evidence_8b9e80efeb67dbb86ab8470f", "evidence_d2abfc6ad9bac74b0bc04f1c", "evidence_dce0b5ff1658b001cac92599", "evidence_f8d2c40766222de2e5a32259"},
        "uncertain": set(),
        "reason": "Manual review: direct AI-tool discussion is included; World Cup and football material is excluded.",
    },
    "ds_20260711_132420_2026_07_11": {
        "included": {"evidence_12782b7b7e92e9be8e06a94e", "evidence_166446dc84551ecdb8727ad1", "evidence_5fb2f91b2df712e34cfd95ea", "evidence_6d260683d1868164f494c79c", "evidence_b4e878f8c8323975af0dc4cb", "evidence_c3b5decc5642822cc80b0d55", "evidence_fde04122d5f8a710b5682bb2"},
        "uncertain": {"evidence_571eee0c9dd6a27543d64cae", "evidence_97dbac716bd78bdfffcb8761"},
        "reason": "Manual review: social-media trends and current public events are included; ambiguous local-event references are uncertain; Wi-Fi hotspots and unrelated posts are excluded.",
    },
    "ds_20260711_211940_cursor": {
        "included": {"evidence_24f1e2d3d28ad5e669ebf570", "evidence_55aac69eb41482e2b6fe5d11", "evidence_785bd4532a44359f08e4c061", "evidence_86ff23a4f46f1668c446ffd0", "evidence_89bfc15ad3cea14f4070e186", "evidence_90347688c7e2b5f2dbb4df54", "evidence_c0ee42fece7b27a62e402c57", "evidence_c4a8c88cc49b59fc5ab1de39", "evidence_d42e863096be3118fb95f5dd", "evidence_db437a046cc99207ac8b9009", "evidence_e0ad9fc15fbb70764adeb45b"},
        "uncertain": {"evidence_1a9bce68a7496927949173b1", "evidence_51acf151bf1173499846412e", "evidence_529a131f7271e959ec1d71bc", "evidence_618dd6f5b0bf3300f13b37c9", "evidence_81982dd549ec1afcf5e06cec", "evidence_91bdbf91bdd38eccf298248c", "evidence_af8651e6ee2226eac53969bc", "evidence_c7ccf56332ab62e980d140c5", "evidence_e212d4445d7456acb3dbb306"},
        "reason": "Manual review: direct Cursor use/tutorial evidence is included; adjacent Claude, Codex, and generic vibe-coding evidence is uncertain.",
    },
}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


for dataset_id, rules in LABELS.items():
    directory = ROOT / dataset_id
    relevance = [json.loads(line) for line in (directory / "relevance_annotations.jsonl").read_text(encoding="utf-8").splitlines() if line]
    for row in relevance:
        row["expected_relevance"] = "included" if row["evidence_id"] in rules["included"] else "uncertain" if row["evidence_id"] in rules["uncertain"] else "excluded"
        row["annotation_reason"] = rules["reason"]
        row["annotator"] = ANNOTATOR
        row["annotated_at"] = TIMESTAMP
    pairs = [json.loads(line) for line in (directory / "duplicate_pair_annotations.jsonl").read_text(encoding="utf-8").splitlines() if line]
    for row in pairs:
        row["expected_relation"] = "not_duplicate"
        row["annotation_reason"] = "Manual review: distinct title/body and no copied-content relationship."
    write_jsonl(directory / "relevance_annotations.completed.jsonl", relevance)
    write_jsonl(directory / "duplicate_pair_annotations.completed.jsonl", pairs)
