"""現行テンプレートに試訳TSVの訳文だけを移した配布用CSVを作る。"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSLATION = ROOT / "translation"


def main():
    with (TRANSLATION / "demo_ja.tsv").open(encoding="utf-8-sig", newline="") as handle:
        sample = {row["id"]: row["ja"] for row in csv.DictReader(handle, delimiter="\t")}
    with (TRANSLATION / "template.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    copied = 0
    for row in rows:
        if row["id"] in sample and row["translate"] == "yes":
            row["ja"] = sample[row["id"]]
            copied += 1
    output = TRANSLATION / "sample_ja.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{output}: {copied} 件の試訳を収録")


if __name__ == "__main__":
    main()
