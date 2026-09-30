"""data.win (GameMaker Studio 1.4 / bytecode 16) の読み取り専用パーサ。

UndertaleModTool を介さずに構造を把握するための最小実装。
書き戻しは行わない（ポインタ再計算が必要なため UTMT 側で行う方針）。
"""
import struct
from dataclasses import dataclass, field


class Data:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.d = f.read()
        assert self.d[:4] == b"FORM", "FORM ヘッダがない"
        self.chunks = {}
        pos = 8
        end = 8 + struct.unpack_from("<I", self.d, 4)[0]
        while pos < end:
            name = self.d[pos:pos + 4].decode("latin1")
            length = struct.unpack_from("<I", self.d, pos + 4)[0]
            self.chunks[name] = (pos + 8, length)
            pos += 8 + length

    # --- primitives ---
    def u32(self, off): return struct.unpack_from("<I", self.d, off)[0]
    def u16(self, off): return struct.unpack_from("<H", self.d, off)[0]
    def i16(self, off): return struct.unpack_from("<h", self.d, off)[0]
    def f32(self, off): return struct.unpack_from("<f", self.d, off)[0]

    def gmstr(self, ptr):
        """文字列ポインタ（データ先頭を指す。長さは -4 の位置）"""
        if not ptr:
            return ""
        n = self.u32(ptr - 4)
        return self.d[ptr:ptr + n].decode("utf-8", "replace")

    def pointer_list(self, chunk):
        off, _ = self.chunks[chunk]
        n = self.u32(off)
        return list(struct.unpack_from("<%dI" % n, self.d, off + 4))

    # --- STRG ---
    def strings(self):
        """STRG の全文字列。戻り値は [(ptr_to_chars, text)]"""
        out = []
        for p in self.pointer_list("STRG"):
            out.append((p + 4, self.gmstr(p + 4)))
        return out

    # --- TPAG ---
    def tpag(self, ptr):
        f = struct.unpack_from("<11H", self.d, ptr)
        return dict(zip(
            ("src_x", "src_y", "src_w", "src_h", "tgt_x", "tgt_y",
             "tgt_w", "tgt_h", "bound_w", "bound_h", "tex_id"), f))

    # --- TXTR ---
    def textures(self):
        """各テクスチャページの PNG バイト列"""
        out = []
        for p in self.pointer_list("TXTR"):
            scaled = self.u32(p)
            blob = self.u32(p + 4)
            # PNG は IEND までを切り出す
            end = self.d.index(b"IEND", blob) + 8
            out.append({"scaled": scaled, "png": self.d[blob:end], "offset": blob})
        return out

    # --- FONT ---
    def fonts(self):
        out = []
        for p in self.pointer_list("FONT"):
            f = {
                "ptr": p,
                "name": self.gmstr(self.u32(p)),
                "family": self.gmstr(self.u32(p + 4)),
                "em_size": self.u32(p + 8),
                "bold": self.u32(p + 12),
                "italic": self.u32(p + 16),
                "range_start": self.u16(p + 20),
                "charset": self.d[p + 22],
                "antialias": self.d[p + 23],
                "range_end": self.u32(p + 24),
                "tpag_ptr": self.u32(p + 28),
                "scale_x": self.f32(p + 32),
                "scale_y": self.f32(p + 36),
            }
            f["tpag"] = self.tpag(f["tpag_ptr"])
            gcount = self.u32(p + 40)
            glyphs = []
            for gp in struct.unpack_from("<%dI" % gcount, self.d, p + 44):
                g = {
                    "ptr": gp,
                    "char": self.u16(gp),
                    "x": self.u16(gp + 2),
                    "y": self.u16(gp + 4),
                    "w": self.u16(gp + 6),
                    "h": self.u16(gp + 8),
                    "shift": self.i16(gp + 10),
                    "offset": self.i16(gp + 12),
                    "kerning_count": self.u16(gp + 14),
                }
                glyphs.append(g)
            f["glyphs"] = glyphs
            out.append(f)
        return out
