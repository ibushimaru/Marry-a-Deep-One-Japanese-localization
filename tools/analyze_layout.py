"""各表示文字列が「どこで・どのフォントで・どの枠幅で」描かれるかを機械的に割り出す。

折り返し方式を人間が推測すると必ず外す（実際に外した）。
バイトコードから確定させて、その結果を翻訳ビルドの入力にする。

## 判定

- `draw_text_ext(x, y, s, sep, w)` … 引数は逆順 push なので、文字列 push の直前が sep、その前が w
- `draw_text(x, y, s)`             … 枠幅なし。レイアウト固定のラベル扱い
- `s_format_text(s, w)`            … 実行時に折り返す。ZWSP 候補を入れればよい
- フォントは同スクリプト内で直前に呼ばれた `s_set_font_l/m/s`

## 出力

    id / var / mode / width / font / en_px / en

`mode` は zwsp / hard / label のいずれか。ビルドはこの表に従って折り返す。
"""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gmcode import Code

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "game_backup" / "data.win.orig"
OUT = ROOT / "translation" / "layout.tsv"

LANG_SCRIPTS = ("gml_Script_s_set_language_UI",
                "gml_Script_s_set_language_help",
                "gml_Script_s_set_language_all")
FONT_CALL = re.compile(r"call s_set_font_(\w+)\(")
PUSHI = re.compile(r"pushi\.i16 (-?\d+)")

# コード中で定数として代入される描画幅（s_variables_1 などで一度だけ設定される）
CONST_VARS = {"v_event_text_width": 516}


def main():
    C = Code(SRC)
    strs = [s for _, s in C.strings()]
    vm = C.varmap()

    # 文字列インデックス -> 変数名
    var_of = {}
    for script in LANG_SCRIPTS:
        e = [x for x in C.entries() if x["name"] == script][0]
        ins = C.disasm(e)
        for i, (off, t) in enumerate(ins):
            if t.startswith("pop.") and i > 0:
                m = re.match(r"push\.str \[(\d+)\]", ins[i - 1][1])
                if m:
                    var_of[int(m.group(1))] = t.split(".")[-1]
    want = {v: k for k, v in var_of.items()}

    # 変数 -> 参照している (スクリプト, 命令オフセット)
    refs = defaultdict(list)
    for a, n in vm.items():
        if n in want:
            e = C.entry_of(a)
            if e and e["name"] not in LANG_SCRIPTS:
                refs[n].append((e["name"], a - e["start"]))

    disasm_cache = {}
    def get(name):
        if name not in disasm_cache:
            e = [x for x in C.entries() if x["name"] == name][0]
            disasm_cache[name] = C.disasm(e)
        return disasm_cache[name]

    rows = []
    for var, sites in sorted(refs.items()):
        idx = want[var]
        found = None
        for script, off in sites:
            ins = get(script)
            pos = next((i for i, (o, _) in enumerate(ins) if o == off), None)
            if pos is None:
                continue
            # 直後の描画/整形呼び出しを探す
            call = None
            for j in range(pos, min(pos + 20, len(ins))):
                t = ins[j][1]
                if "call draw_text_ext" in t or "call draw_text(" in t or "call s_format_text" in t:
                    call = t
                    break
            if not call:
                continue
            # フォント（同スクリプト内で直前の s_set_font）
            font = None
            for j in range(pos, -1, -1):
                m = FONT_CALL.search(ins[j][1])
                if m:
                    font = "font_" + m.group(1)
                    break
            # 幅（文字列 push の直前の pushi 2つ: sep, w の順に現れる）
            nums = []
            for j in range(pos - 1, max(-1, pos - 10), -1):
                m = PUSHI.search(ins[j][1])
                if m:
                    nums.append(int(m.group(1)))
                elif "push.var" in ins[j][1]:
                    n = ins[j][1].split(".")[-1]
                    if n in CONST_VARS:
                        nums.append(CONST_VARS[n])
                if len(nums) >= 2:
                    break
            if "s_format_text" in call:
                found = ("zwsp", CONST_VARS.get("v_event_text_width"), font, script)
            elif "draw_text_ext" in call:
                found = ("hard", nums[1] if len(nums) > 1 else None, font, script)
            else:
                found = ("label", None, font, script)
            break
        if not found:
            continue
        mode, width, font, script = found
        rows.append({"id": idx, "var": var, "mode": mode,
                     "width": width or "", "font": font or "font_m",
                     "script": script, "en": strs[idx][:60]})

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "var", "mode", "width", "font", "script", "en"],
                           delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    from collections import Counter
    print(f"描画経路を特定: {len(rows)}件 / 表示文字列 {len(var_of)}件")
    for m, c in Counter(r["mode"] for r in rows).most_common():
        print(f"   {m}: {c}件")
    print("   幅の分布:", Counter(str(r["width"]) for r in rows if r["mode"] == "hard").most_common(8))
    print("   フォント:", Counter(r["font"] for r in rows).most_common())
    print(f"出力: {OUT}")


if __name__ == "__main__":
    main()
