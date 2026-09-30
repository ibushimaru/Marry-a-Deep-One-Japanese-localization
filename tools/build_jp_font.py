"""日本語フォントを本番仕様で data.win に組み込む。

各 GM フォント（font_l / font_m / font_s とそれぞれの antialias 版）について:

1. 専用のテクスチャページを新規に作る
2. 元のラテン文字ブロック（512x256）をページの (0,0) にそのままコピーする
   → 既存グリフの x/y 座標が変わらないので、ラテン側のグリフ構造体は原本を再利用できる
      （カーニング情報も保たれる）
3. その下に日本語グリフを敷き詰める
4. 新しいフォント構造体（グリフポインタ表つき）を AUDO 追記領域に確保し、
   FONT チャンクのポインタを差し替える
5. フォントの TPAG エントリを新ページ全体を指すよう同サイズで書き換える

グリフのポインタ表は **char コードの昇順** でなければならない。
崩れるとランタイムの二分探索が外れ、豆腐ではなく空白になる。
"""
import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from gmpack import Packer

ROOT = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent)
SRC = ROOT / "game_backup" / "data.win.orig"
OUT = ROOT / "build" / "data.win"
FONTS = ROOT / "fonts"

ZWSP = 0x200B

# GM フォント名 -> (日本語フォントファイル, サイズ補正)
JP_FONT_FOR = {
    "font_l": "ShipporiMincho-SemiBold.ttf",            # 原本 Grenze SemiBold
    "font_l_antialias": "ShipporiMincho-SemiBold.ttf",
    "font_m": "ShipporiMincho-Medium.ttf",              # 原本 Grenze Regular
    "font_m_antialias": "ShipporiMincho-Medium.ttf",
    "font_s": "ShipporiMincho-Medium.ttf",              # 原本 Grenze Medium
    "font_s_antialias": "ShipporiMincho-Medium.ttf",
}

# 明朝は記号（＜＞℃≠∞㎡ 等）の収録が薄いので、無い字はゴシックで補う。
# 記号がわずかにゴシックになるが、豆腐になるよりはるかにまし。
FALLBACK_TTF = FONTS / "UDEVGothicHS-Regular.ttf"

PAGE_W, PAGE_H = 4096, 2048
LATIN_W, LATIN_H = 512, 256


def jp_charset(ttf_path, fallback_path=None):
    """収録する日本語文字を決める。

    判定は **cp932**（Windows 版シフトJIS）で行う。厳密な shift_jis だと
    IME が出す波ダッシュ U+FF5E や全角ハイフン U+FF0D が落ちてしまい、
    翻訳者の入力環境によって豆腐が出る。
    """
    from fontTools.ttLib import TTFont
    cmap = set(TTFont(ttf_path, lazy=True).getBestCmap())
    if fallback_path and Path(fallback_path).exists():
        cmap |= set(TTFont(fallback_path, lazy=True).getBestCmap())

    def encodable(cp):
        try:
            chr(cp).encode("cp932")
            return True
        except UnicodeEncodeError:
            return False

    chars = []
    ranges = [
        (0x2000, 0x22FF),   # 一般記号・数学記号（℃ ≠ ∞ …）
        (0x2460, 0x24FF),   # 丸数字
        (0x2500, 0x26FF),   # 罫線・図形
        (0x3000, 0x303F),   # 句読点・括弧類
        (0x3041, 0x309F),   # ひらがな
        (0x30A0, 0x30FF),   # カタカナ
        (0x3200, 0x33FF),   # ㈱ ㎡ など
        (0x4E00, 0x9FFF),   # 漢字
        (0xFF01, 0xFF60),   # 全角英数記号
        (0xFFE0, 0xFFE6),   # 全角通貨記号
    ]
    for lo, hi in ranges:
        for cp in range(lo, hi + 1):
            if cp in cmap and encodable(cp):
                chars.append(cp)
    # cp932 では表せないが、日本語の組版で普通に使う記号
    for cp in (0x2013, 0x2014, 0x2015, 0x2025, 0x2212):
        if cp in cmap:
            chars.append(cp)
    return sorted(set(chars))


def measure_font(D, f, page_img):
    """原本のグリフからベースライン位置とキャップ高を実測する"""
    ft = f["tpag"]
    ox, oy = ft["src_x"], ft["src_y"]
    g = {x["char"]: x for x in f["glyphs"]}
    A = g[ord("A")]
    cell = page_img.crop((ox + A["x"], oy + A["y"], ox + A["x"] + A["w"], oy + A["y"] + A["h"]))
    alpha = cell.split()[3]
    rows = [y for y in range(cell.height) if any(alpha.getpixel((x, y)) > 0 for x in range(cell.width))]
    return {
        "cap_cell_h": A["h"],            # ラテン大文字セルの高さ（= 行頭からベースラインまで）
        "cap_ink": rows[-1] - rows[0] + 1,
        "baseline": A["h"] - 1,          # セル内のベースライン行
        "descender_h": max(x["h"] for x in f["glyphs"]),
    }


def main(dry_run=False, translate=None):
    P = Packer(SRC)
    fonts = P.fonts()
    texs = P.textures()
    pages = [Image.open(io.BytesIO(t["png"])).convert("RGBA") for t in texs]

    charset = jp_charset(FONTS / JP_FONT_FOR["font_m"], FALLBACK_TTF)
    print(f"収録する日本語グリフ: {len(charset)}字 (+ZWSP)")
    print(f"  ひらがな/カタカナ/記号: {sum(1 for c in charset if c < 0x4E00)}")
    print(f"  漢字: {sum(1 for c in charset if 0x4E00 <= c <= 0x9FFF)}")

    new_pages = []      # (font_name, PIL.Image)
    font_structs = []   # 新しい FONT ポインタ

    for f in fonts:
        m = measure_font(P, f, pages[f["tpag"]["tex_id"]])
        jp_px = f["em_size"] + 1
        adv = jp_px
        cell_w, cell_h = adv, m["cap_cell_h"] + 4      # ベースライン下に4px余裕を持たせる
        cols = PAGE_W // (cell_w + 1)
        rows_needed = -(-(len(charset)) // cols)
        need_h = LATIN_H + rows_needed * (cell_h + 1)
        print(f"  {f['name']:18s} JP={jp_px}px 送り={adv} セル={cell_w}x{cell_h} "
              f"{cols}列x{rows_needed}行 必要高={LATIN_H}+{rows_needed*(cell_h+1)}={need_h}")
        if need_h > PAGE_H:
            raise RuntimeError(f"{f['name']}: ページ高 {PAGE_H} に収まらない（{need_h} 必要）")
        if dry_run:
            continue

        # --- 新ページを作り、ラテンブロックをそのままコピー ---
        img = Image.new("RGBA", (PAGE_W, PAGE_H), (255, 255, 255, 0))
        ft = f["tpag"]
        latin = pages[ft["tex_id"]].crop(
            (ft["src_x"], ft["src_y"], ft["src_x"] + LATIN_W, ft["src_y"] + LATIN_H))
        img.paste(latin, (0, 0))

        # --- 日本語グリフを描く ---
        primary_path = FONTS / JP_FONT_FOR[f["name"]]
        jpfont = ImageFont.truetype(str(primary_path), jp_px)
        from fontTools.ttLib import TTFont as _TTF
        primary_cmap = set(_TTF(primary_path, lazy=True).getBestCmap())
        fbfont = (ImageFont.truetype(str(FALLBACK_TTF), jp_px)
                  if FALLBACK_TTF.exists() else jpfont)
        fb_used = 0
        draw = ImageDraw.Draw(img)
        glyph_ptrs = []
        for i, cp in enumerate(charset):
            cx = (i % cols) * (cell_w + 1)
            cy = LATIN_H + (i // cols) * (cell_h + 1)
            # ベースラインをラテン文字と揃える（セル上端から baseline 行目）
            use = jpfont
            if cp not in primary_cmap:
                use = fbfont
                fb_used += 1
            draw.text((cx + cell_w // 2, cy + m["baseline"]), chr(cp), font=use,
                      fill=(255, 255, 255, 255), anchor="ms")
            glyph_ptrs.append(P.alloc(struct.pack(
                "<HHHHHhhH", cp, cx, cy, cell_w, cell_h, adv, 0, 0)))

        # ZWSP: 幅も送りも 0 の不可視グリフ（折り返し候補マーカー）
        zwsp_ptr = P.alloc(struct.pack("<HHHHHhhH", ZWSP, 0, 0, 0, 0, 0, 0, 0))

        # --- グリフポインタ表を char 昇順で組む ---
        latin_ptrs = [(g["char"], g["ptr"]) for g in f["glyphs"]]
        jp_ptrs = list(zip(charset, glyph_ptrs)) + [(ZWSP, zwsp_ptr)]
        allp = sorted(latin_ptrs + jp_ptrs)
        codes = [c for c, _ in allp]
        assert all(codes[i] < codes[i + 1] for i in range(len(codes) - 1)), "char昇順でない"

        # --- TPAG を新ページ全体に向ける（同サイズで in-place 書き換え）---
        page_index = len(texs) + len(new_pages)
        P.patch(f["tpag_ptr"], struct.pack(
            "<11H", 0, 0, PAGE_W, PAGE_H, 0, 0, PAGE_W, PAGE_H, PAGE_W, PAGE_H, page_index))

        # --- 新しいフォント構造体を追記領域に確保 ---
        head = struct.pack(
            "<IIIIIHBBIIffI",
            P.u32(f["ptr"]), P.u32(f["ptr"] + 4),      # Name, DisplayName
            f["em_size"], f["bold"], f["italic"],
            f["range_start"], f["charset"], f["antialias"],
            0xFFFF,                                     # RangeEnd を拡張
            f["tpag_ptr"], f["scale_x"], f["scale_y"],
            len(allp))
        body = struct.pack("<%dI" % len(allp), *[p for _, p in allp])
        font_structs.append(P.alloc(head + body))
        new_pages.append((f["name"], img))
        if fb_used:
            print(f"      （うち {fb_used}字はゴシックで補完）")

    if dry_run:
        return

    # --- FONT チャンクのポインタ表を差し替え ---
    P.set_pointer_list("FONT", font_structs)

    # --- TXTR: 既存6ページ + 新規6ページ ---
    tex_entries = []
    for t in texs:
        tex_entries.append(P.alloc(struct.pack("<II", t["scaled"], t["offset"])))
    for name, img in new_pages:
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=9, optimize=True)
        png = buf.getvalue()
        blob = P.alloc(png)
        tex_entries.append(P.alloc(struct.pack("<II", 0, blob)))
        print(f"  新ページ {name}: {PAGE_W}x{PAGE_H} PNG {len(png):,}B")
    P.set_pointer_list("TXTR", tex_entries)

    # --- 折り返しマーカーを ZWSP にする ---
    # [12085] は元は '`'。参照は s_get_key（画面キーボード）1箇所のみで、そこが
    # ZWSP を返すようになるだけなので実害がない。
    P.relocate_string(12085, "​")
    for addr in (7193648, 7193820):      # s_format_text @280 / @452 の比較対象
        cur = struct.unpack_from("<I", P.buf, addr)[0]
        assert cur == 252, f"@{addr} が想定と違う: [{cur}]"
        struct.pack_into("<I", P.buf, addr, 12085)
    print("  折り返し判定: ' ' -> ZWSP (U+200B)")

    if translate:
        import csv
        import jp_text

        # P.fonts() は原本を読むのでラテン文字の送り幅しか入っていない。
        # 日本語は今回書き込んだ送り幅（em_size + 1）を明示的に足す必要がある。
        advances, en_adv = {}, {}
        for f in P.fonts():
            en_adv[f["name"]] = {chr(g["char"]): g["shift"] for g in f["glyphs"]}
            a = dict(en_adv[f["name"]])
            a.update({chr(cp): f["em_size"] + 1 for cp in charset})
            a[jp_text.ZWSP] = 0
            advances[f["name"]] = a

        # 描画経路は analyze_layout.py が確定させたものを使う（推測しない）
        layout = {}
        lp = ROOT / "translation" / "layout.tsv"
        if lp.exists():
            for r in csv.DictReader(open(lp, encoding="utf-8"), delimiter="\t"):
                layout[int(r["id"])] = (r["mode"], int(r["width"]) if r["width"] else None,
                                        r["font"] or "font_m")

        strs = [x for _, x in P.strings()]
        # 翻訳者から戻る translation.csv（カンマ区切り・BOM付き）と
        # 手元の作業用 TSV の両方を受け付ける
        is_csv = str(translate).lower().endswith(".csv")
        enc = "utf-8-sig" if is_csv else "utf-8"
        rows = list(csv.DictReader(open(translate, encoding=enc),
                                   delimiter="," if is_csv else "\t"))
        applied, warn = 0, 0
        allowed = set(charset)
        for r in rows:
            ja = (r.get("ja") or "").strip()
            if not ja:
                continue
            idx = int(r["id"])
            if (r.get("translate") or "yes").strip() == "no":
                print(f"  ! [{idx}] translate=no の行に訳文が入っている。飛ばす: {ja[:20]}")
                continue
            override = (r.get("wrap") or "").strip()
            if not override and r.get("route"):
                # translation.csv の route 列から折り返し方式を決める
                rt = r["route"].strip()
                if rt == "event":
                    override = "zwsp"
                elif rt == "draw_ext" and r.get("width_px"):
                    override = f"hard:{r['width_px']}:{r.get('font') or 'font_m'}"
                elif rt in ("label", "name"):
                    override = "none"
            if override:                       # TSV 側で明示指定した場合はそちらを優先
                if override == "none":
                    mode, width, font = "label", None, "font_l"
                elif override.startswith("hard:"):
                    _, w, fn = override.split(":")
                    mode, width, font = "hard", int(w), fn
                else:
                    mode, width, font = "zwsp", 516, "font_m"
            else:
                mode, width, font = layout.get(idx, ("label", None, "font_l"))

            miss = jp_text.missing_glyphs(ja, allowed)
            if miss:
                print(f"  ! [{idx}] アトラスに無い文字: {''.join(miss)}")
                warn += 1

            if mode == "hard" and width:
                text = jp_text.hard_wrap(ja, advances[font], width)
                over = [l for l in text.split("#")
                        if sum(advances[font].get(c, 0) for c in l) > width]
                if over:
                    print(f"  ! [{idx}] 折り返し後も枠超過: {over}")
                    warn += 1
            elif mode == "zwsp":
                text = jp_text.mark(ja, advances[font], width or 516)
            else:
                # ラベルの実際の表示枠幅は不明なので、原文幅から超過を推定しない
                text = ja
            P.relocate_string(idx, text)
            applied += 1
        print(f"  訳文を適用: {applied}件 / 警告 {warn}件")

    OUT.parent.mkdir(exist_ok=True)
    added = P.save(OUT)
    print(f"\n追記サイズ: {added:,}B")
    print(f"出力: {OUT} ({OUT.stat().st_size:,}B / 原本 {SRC.stat().st_size:,}B)")
    for name, ln in P.verify():
        pass
    print("FORM/チャンク境界の検証: OK")


if __name__ == "__main__":
    tsv = None
    for i, a in enumerate(sys.argv):
        if a == "--translate" and i + 1 < len(sys.argv):
            tsv = sys.argv[i + 1]
    main(dry_run="--dry-run" in sys.argv, translate=tsv)
