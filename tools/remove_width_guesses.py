"""配布CSVから実測に基づかない字数列を削除する一回限りの移行。"""

import csv
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "translation"
DROP = {"chars_per_line", "max_chars"}


def migrate(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    if not fields or not DROP.issubset(fields):
        raise ValueError(f"移行前の列がそろっていません: {path}")
    kept = [field for field in fields if field not in DROP]
    temporary = path.with_name(path.name + ".new")
    if temporary.exists():
        raise FileExistsError(temporary)
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=kept)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in kept})
    os.replace(temporary, path)
    print(f"{path.name}: {len(rows)} 行、{len(kept)} 列")


if __name__ == "__main__":
    for filename in ("template.csv", "translation.csv"):
        migrate(ROOT / filename)
