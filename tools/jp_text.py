"""訳文に折り返し候補（ZWSP）を挿入するテキスト処理。

翻訳者は**改行を一切気にせず**普通の日本語を書けばよい。
このツールが ZWSP を入れ、実際にどこで折り返すかはゲームが `string_width` で実測して決める。

## 処理の流れ

1. 日本語の連続部分は BudouX で文節に分け、その境界を候補にする
2. 英字の連続部分は半角スペースの後ろを候補にする（英語も折り返せるようにする）
3. 禁則処理で不適切な候補を取り除く
4. 数字・漢数字の連なりを保護する（「一九三一年」が割れるのを防ぐ）
5. 1候補間の幅が行幅を超える場合は、その内部に1文字単位の候補を足す（あふれ防止）
"""
import re
import unicodedata

import budoux

ZWSP = "​"

# 行頭に来てはいけない文字
NO_LINE_START = (
    "。、，．・：；？！‼⁇⁈⁉"
    "ヽヾゝゞ々ー–—"
    "）］｝〕〉》」』】〙〗〟’”｠»)]}"
    "ぁぃぅぇぉっゃゅょゎゕゖ"
    "ァィゥェォッャュョヮヵヶ"
    "％‰℃°"
)
# 行末に来てはいけない文字
NO_LINE_END = "（［｛〔〈《「『【〘〖〝‘“｟«([{￥＄£€#"

# 途中で割ってはいけない連なり（数値・年号など）
UNBREAKABLE = re.compile(
    r"[0-9０-９一二三四五六七八九〇十百千万億兆]+"
    r"[年月日時分秒円人回番歳才個台本冊枚度％%]*"
)

_parser = None


def _budoux():
    global _parser
    if _parser is None:
        _parser = budoux.load_default_japanese_parser()
    return _parser


def _is_jp(ch):
    if ch in "　​":
        return False
    cp = ord(ch)
    return (0x3040 <= cp <= 0x30FF or 0x4E00 <= cp <= 0x9FFF
            or 0xFF01 <= cp <= 0xFF60 or 0x3000 <= cp <= 0x303F)


def _candidate_positions(text):
    """折り返してよい位置（その位置の直前で改行できる）の集合を返す"""
    pos = set()
    # 日本語の連続部分は BudouX の文節境界
    for m in re.finditer(r"[^\x00-\x7F]+", text):
        start = m.start()
        offset = 0
        for phrase in _budoux().parse(m.group()):
            offset += len(phrase)
            if offset < len(m.group()):
                pos.add(start + offset)
    # 半角スペースの直後（英文の折り返し）
    for m in re.finditer(r" +", text):
        pos.add(m.end())
    return pos


def _apply_kinsoku(text, pos):
    """禁則処理と数値保護で候補を間引く"""
    ok = set()
    protected = set()
    for m in UNBREAKABLE.finditer(text):
        protected.update(range(m.start() + 1, m.end()))
    for p in pos:
        if p <= 0 or p >= len(text):
            continue
        if p in protected:
            continue
        if text[p] in NO_LINE_START:      # 次の行の先頭になる文字
            continue
        if text[p - 1] in NO_LINE_END:    # 前の行の末尾になる文字
            continue
        ok.add(p)
    return ok


def _enforce_max_run(text, pos, advance, max_px):
    """候補間の幅が行幅を超える場合、内部に1文字単位の候補を足す"""
    if not max_px:
        return pos
    pos = set(pos)
    bounds = sorted(pos | {0, len(text)})
    for a, b in zip(bounds, bounds[1:]):
        run = text[a:b]
        if sum(advance.get(c, 0) for c in run) <= max_px:
            continue
        w = 0
        for i, ch in enumerate(run):
            w += advance.get(ch, 0)
            if w > max_px and a + i > a:
                if text[a + i] not in NO_LINE_START and text[a + i - 1] not in NO_LINE_END:
                    pos.add(a + i)
                    w = advance.get(ch, 0)
    return pos


def mark(text, advance=None, max_px=None):
    """折り返し候補に ZWSP を挿入した文字列を返す"""
    if ZWSP in text:
        text = text.replace(ZWSP, "")
    pos = _apply_kinsoku(text, _candidate_positions(text))
    if advance and max_px:
        pos = _enforce_max_run(text, pos, advance, max_px)
    out = []
    for i, ch in enumerate(text):
        if i in pos:
            out.append(ZWSP)
        out.append(ch)
    return "".join(out)


def hard_wrap(text, advance, width):
    """`#`（このゲームの改行記号）を実際に打ち込んで折り返す。

    `s_format_text` を通らず `draw_text_ext` が直接描く箇所用。
    GM 内蔵の折り返しは半角スペースでしか改行しないため、日本語は
    オフラインで改行位置を確定させるしかない。
    """
    out = []
    for para in text.split("#"):
        pos = sorted(_apply_kinsoku(para, _candidate_positions(para)) | {len(para)})
        line, start = "", 0
        for p in pos:
            seg = para[start:p]
            if line and sum(advance.get(c, 0) for c in line + seg) >= width:
                out.append(line)
                line = seg
            else:
                line += seg
            start = p
        out.append(line)
    return "#".join(out)


def missing_glyphs(text, available):
    """アトラスに無い文字を洗い出す"""
    return sorted({c for c in text
                   if ord(c) > 0x7F and ord(c) not in available and c != ZWSP})


def preview(text, advance, width):
    """ゲームと同じ手順で折り返しを再現する（検算用）"""
    marked = text if ZWSP in text else mark(text, advance, width)
    lines, line = [], ""
    for seg in marked.split(ZWSP):
        if line and sum(advance.get(c, 0) for c in line + seg) >= width:
            lines.append(line)
            line = seg
        else:
            line += seg
    lines.append(line)
    return lines
