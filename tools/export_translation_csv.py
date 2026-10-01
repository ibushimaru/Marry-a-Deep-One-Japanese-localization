"""翻訳者に渡す CSV を全数生成する。

言語テーブル経由だけでなく、スクリプトに直書きされたリテラル（チュートリアル・物語）も
バイトコードから追跡し、**どの経路で・どの枠幅で描かれるか**を確定させる。

## 追跡のしかた

1. まず「変数 -> 描画方法」の対応表を作る。
   全スクリプトの変数参照を走査し、その直後（20命令以内）に描画呼び出しがあれば記録する。
   - `draw_text_ext(x,y,s,sep,w)` … 引数は逆順 push。文字列の直前が sep、その前が w
   - `draw_text(x,y,s)`           … 枠幅なし。レイアウト固定のラベル
   - `s_format_text(s,w)`         … 実行時に折り返す
2. 次に文字列リテラルを走査し、行き先を判定する。
   - `s_event_0_open` に渡る        -> イベント経路（実行時折り返し / 516px / font_m）
   - `ds_list_add` でリストに入る   -> リストの消費側を見る（多くはイベント経路）
   - 変数に代入される               -> 1. の表で引く
3. `add` を挟んで別の値が連結される場合は連結フラグを立てる。
   挿入部の長さが実行時まで決まらないので自動折り返しができない。

## 安全判定

コードからの参照が2箇所以上ある文字列は、表示と内部状態値を兼ねている可能性がある。
（`text_on = 'on'` の 'on' は `if 変数 == "on"` の比較にも使われる）
これらは `translate` 列を no にして、訳さないよう明示する。
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
OUT = ROOT / "translation" / "translation.csv"

EVENT_WIDTH, EVENT_FONT = 516, "font_m"      # v_event_text_width（s_variables_1 で固定）

FONT_CALL = re.compile(r"call s_set_font_(\w+)\(")
PUSHI = re.compile(r"pushi\.i16 (-?\d+)")
PUSHSTR = re.compile(r"push\.str \[(\d+)\]")
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z_0-9]*\}")
IDENTIFIER = re.compile(r"^[a-z0-9_]+$")

LANG_SCRIPTS = {"gml_Script_s_set_language_UI": "ui",
                "gml_Script_s_set_language_help": "help",
                "gml_Script_s_set_language_all": "misc"}


def category_of(script):
    if script in LANG_SCRIPTS:
        return LANG_SCRIPTS[script]
    n = script.replace("gml_Script_", "")
    if n.startswith("s_STORY"):
        return "story"
    if "tutorial" in n:
        return "tutorial"
    if n.startswith("s_GENERATE_name"):
        return "name"
    if n.startswith("s_fill_"):
        return "flavor"
    if "journal" in n:
        return "journal"
    if "action" in n:
        return "action"
    return "misc"


def looks_like_text(s):
    """内部キーではなく、人間に見せる文章・ラベルらしいか"""
    t = s.strip()
    if len(t) < 2 or len(t) > 4000:
        return False
    if IDENTIFIER.match(t):            # snake_case の内部キー
        return False
    if not re.search(r"[A-Za-z]", t):  # 英字を含まないものは記号・数値
        return False
    return bool(re.search(r"[A-Z]", t) or " " in t or len(t) > 12)


def main():
    C = Code(SRC)
    strs = [s for _, s in C.strings()]
    vm = C.varmap()

    disasm, index = {}, {}
    for e in C.entries():
        ins = C.disasm(e)
        disasm[e["name"]] = ins
        index[e["name"]] = {o: i for i, (o, _) in enumerate(ins)}

    def font_before(ins, pos):
        for j in range(pos, -1, -1):
            m = FONT_CALL.search(ins[j][1])
            if m:
                return "font_" + m.group(1)
        return None

    def sink_after(ins, pos, span=24):
        """描画/整形/イベント呼び出しと、連結の有無を返す"""
        concat = False
        for j in range(pos, min(pos + span, len(ins))):
            t = ins[j][1]
            if t.startswith("add"):
                concat = True
            if "call s_event_0_open" in t:
                return "event", concat, j
            if "call s_format_text" in t:
                return "format", concat, j
            if "call draw_text_ext" in t:
                return "draw_ext", concat, j
            if "call draw_text(" in t:
                return "draw", concat, j
            if "call ds_list_add" in t:
                return "list", concat, j
        return None, concat, None

    def width_before(ins, pos):
        """文字列 push の直前にある pushi 2つ（sep, w の順）から枠幅を取る"""
        nums = []
        for j in range(pos - 1, max(-1, pos - 12), -1):
            m = PUSHI.search(ins[j][1])
            if m:
                nums.append(int(m.group(1)))
            elif "v_event_text_width" in ins[j][1]:
                nums.append(EVENT_WIDTH)
            if len(nums) >= 2:
                break
        return nums[1] if len(nums) > 1 else None

    # --- 1. 変数 -> 描画方法 ---
    # self 変数は全体で、ローカル変数はスクリプト内でしか通用しないので分けて持つ
    var_route, local_route = {}, {}
    for addr, name in vm.items():
        e = C.entry_of(addr)
        if not e:
            continue
        ins = disasm[e["name"]]
        pos = index[e["name"]].get(addr - e["start"])
        if pos is None:
            continue
        kind, _, cpos = sink_after(ins, pos)
        if kind == "draw_ext":
            r = ("draw_ext", width_before(ins, pos), font_before(ins, pos) or "font_m")
        elif kind == "draw":
            r = ("label", None, font_before(ins, pos) or "font_l")
        elif kind in ("format", "event", "list"):
            r = ("event", EVENT_WIDTH, EVENT_FONT)
        else:
            continue
        if e["name"] not in LANG_SCRIPTS:
            var_route.setdefault(name, r)
        local_route.setdefault((e["name"], name), r)

    # --- 2. 文字列リテラル -> 行き先 ---
    refs = Counter()
    for name, ins in disasm.items():
        for _, t in ins:
            m = PUSHSTR.match(t)
            if m:
                refs[int(m.group(1))] += 1

    rows, seen = [], set()
    for name, ins in disasm.items():
        for i, (off, t) in enumerate(ins):
            m = PUSHSTR.match(t)
            if not m:
                continue
            idx = int(m.group(1))
            s = strs[idx]
            if idx in seen or not looks_like_text(s):
                continue
            kind, concat, cpos = sink_after(ins, i)
            route, width, font = None, None, None
            if kind in ("event", "list", "format"):
                route, width, font = "event", EVENT_WIDTH, EVENT_FONT
            elif kind == "draw_ext":
                route, width, font = "draw_ext", width_before(ins, i), font_before(ins, i) or "font_m"
            elif kind == "draw":
                route, width, font = "label", None, font_before(ins, i) or "font_l"
            else:
                # 代入先の変数から引く（同スクリプトのローカル -> 全体の self 変数）
                for j in range(i + 1, min(i + 6, len(ins))):
                    if ins[j][1].startswith("pop"):
                        v = ins[j][1].split(".")[-1]
                        r = local_route.get((name, v)) or var_route.get(v)
                        if r:
                            route, width, font = r
                        break
            if route is None:
                if " " in s.strip() and len(s) > 20:
                    # 文章なら物語文としてイベント経路（実行時折り返し）を既定にする。
                    # 物語・日誌の類はほぼすべてイベントUIに流れるため実害が小さい。
                    route, width, font = "event", EVENT_WIDTH, EVENT_FONT
                elif name in LANG_SCRIPTS:
                    # 言語テーブルにある時点で表示文字列と確定しているので、
                    # 経路が追えなくてもラベルとして必ず載せる（落とすと翻訳漏れになる）
                    route, width, font = "label", None, "font_l"
                else:
                    continue
            seen.add(idx)
            ph = PLACEHOLDER.findall(s)

            # 固有名詞は本文ではないので経路の意味がない
            if category_of(name) == "name":
                route, width, font = "name", None, ""

            rows.append({
                "id": idx,
                "category": category_of(name),
                "script": name.replace("gml_Script_", ""),
                "route": route,
                "width_px": width or "",
                "font": font or "",
                "concat": "yes" if concat else "",
                "placeholders": " ".join(sorted(set(ph))),
                "translate": "no" if refs[idx] > 1 else "yes",
                "en": s,
                "ja": "",
            })

    rows.sort(key=lambda r: (r["category"], r["script"], r["id"]))
    OUT.parent.mkdir(exist_ok=True)
    cols = ["id", "category", "script", "route", "width_px", "font",
            "concat", "placeholders", "translate", "en", "ja"]
    # Excel で開けるよう BOM 付き UTF-8
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["translate"] == "yes"]
    chars = sum(len(r["en"]) for r in ok)
    print(f"出力: {OUT}")
    print(f"  総行数 {len(rows)} / 翻訳対象 {len(ok)} / 原文 {chars:,}字")
    print("  経路:", dict(Counter(r["route"] for r in rows)))
    print("  区分:", dict(Counter(r["category"] for r in rows)))
    print(f"  連結あり: {sum(1 for r in rows if r['concat'])}件")
    print(f"  プレースホルダーあり: {sum(1 for r in rows if r['placeholders'])}件")
    print(f"  訳さないほうがよい: {len(rows)-len(ok)}件")


if __name__ == "__main__":
    main()
