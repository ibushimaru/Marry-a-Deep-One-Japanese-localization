"""翻訳対象の文字列を安全判定つきで抽出し、TSV に書き出す。

## 安全判定

このゲームは表示文字列と内部状態値が同じ STRG エントリを共有していることがある。
たとえば `text_on = 'on'` の 'on' は `if 変数 == "on"` の比較にも使われており、
これを訳すと分岐が壊れる。

そこで **コードからの参照が1箇所だけで、かつそれが表示文字列への代入である**
インデックスのみを翻訳対象とする。参照が複数あるものは内部値として使われている
可能性があるため除外する。

## 出力

    id / kind / var / refs / en / ja / note

`ja` 列を翻訳者が埋める。`id` は STRG インデックスで、これがビルド時のキーになる。
"""
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gmcode import Code

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "game_backup" / "data.win.orig"
OUT = ROOT / "translation" / "strings.tsv"

# 表示文字列を変数に代入しているスクリプト
LANG_SCRIPTS = {
    "gml_Script_s_set_language_UI": "ui",
    "gml_Script_s_set_language_help": "help",
    "gml_Script_s_set_language_all": "misc",
}


def code_string_refs(C):
    """文字列インデックス -> [(スクリプト名, 命令オフセット)]"""
    refs = defaultdict(list)
    d = C.d
    for e in C.entries():
        a = e["start"]
        while a < e["end"]:
            b2, op = d[a + 2], d[a + 3]
            size = 4
            if op in (0xC0, 0xC1, 0xC2, 0xC3, 0x84):
                size = 4 + {0x00: 8, 0x03: 8, 0x0F: 0}.get(b2, 4)
                if b2 == 0x06:
                    refs[C.u32(a + 4)].append((e["name"], a - e["start"]))
            elif op == 0x45:
                size = 4 if (b2 & 0x0F) == 0x0F else 8
            elif op == 0xD9:
                size = 8
            a += size
    return refs


def name_pointers(C):
    """アセット名などとして絶対ポインタ参照されている文字列のインデックス"""
    ptr2idx = {p: i for i, (p, _) in enumerate(C.strings())}
    used = set()
    for ch in ("SPRT", "SOND", "FONT", "SCPT", "OBJT", "ROOM", "BGND",
               "PATH", "TMLN", "SHDR", "AGRP", "EXTN", "CODE"):
        if ch not in C.chunks:
            continue
        for p in C.pointer_list(ch):
            for off in (0, 4):
                q = C.u32(p + off)
                if q in ptr2idx:
                    used.add(ptr2idx[q])
    return used


def main():
    C = Code(SRC)
    S = C.strings()
    strs = [s for _, s in S]
    refs = code_string_refs(C)
    names = name_pointers(C)

    # 言語スクリプト内の「push.str -> pop.var」から 変数名 を拾う
    assigned = {}
    for script, kind in LANG_SCRIPTS.items():
        e = [x for x in C.entries() if x["name"] == script][0]
        ins = C.disasm(e)
        for i, (off, t) in enumerate(ins):
            if t.startswith("pop.") and i > 0:
                m = re.match(r"push\.str \[(\d+)\]", ins[i - 1][1])
                if m:
                    assigned[int(m.group(1))] = (kind, t.split(".")[-1])

    rows = []
    for idx, (kind, var) in sorted(assigned.items()):
        s = strs[idx]
        n = len(refs.get(idx, []))
        note = []
        if n > 1:
            note.append(f"内部値と共有の疑い({n}箇所で参照)")
        if idx in names:
            note.append("アセット名としても参照")
        if not s.strip():
            note.append("空文字列")
        rows.append({
            "id": idx, "kind": kind, "var": var, "refs": n,
            "en": s, "ja": "", "note": " / ".join(note),
        })

    safe = [r for r in rows if not r["note"]]
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "kind", "var", "refs", "en", "ja", "note"],
                           delimiter="\t", quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)

    print(f"抽出: {len(rows)}件 / うち安全に翻訳できるもの {len(safe)}件")
    print(f"  除外理由の内訳:")
    for reason, c in Counter(r["note"] for r in rows if r["note"]).most_common():
        print(f"    {reason}: {c}件")
    print(f"出力: {OUT}")


if __name__ == "__main__":
    main()
