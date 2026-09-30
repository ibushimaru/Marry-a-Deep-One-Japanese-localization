"""data.win を「チャンクを太らせずに」書き換えるためのリパッカ。

## 仕組み

GameMaker のチャンク内ポインタは**ファイル絶対オフセット**で、ランタイムはファイル全体を
メモリに読み込んでから参照する。したがって構造体はファイル内のどこに置いても動く。

これを利用して、新規・拡張したい構造体を**末尾の AUDO チャンクを伸ばした領域**に置く。

- AUDO の中身は `count=0` だけ。GM は count を読んで残りを無視するので、後ろに何を置いても安全
- 既存チャンクは**宣言サイズも位置も一切変わらない**ため、全ポインタの再計算が不要
- 書き換えるのは各チャンク先頭のポインタ表など、同サイズで収まる部分のみ

チャンク先頭のポインタ表を書き直すと、その後ろにあった元の構造体は上書き／取り残されるが、
参照されなくなるだけなので問題ない（チャンクの宣言長は変えないので次チャンクの位置も動かない）。

## 使い方

    P = Packer("data.win.orig")
    addr = P.alloc(struct.pack(...))          # AUDO 領域に確保
    sptr = P.alloc_string("テキスト")          # GM 文字列として確保（char データの位置を返す）
    P.set_pointer_list("TXTR", [a1, a2, ...]) # チャンク先頭の count+ポインタ表を差し替え
    P.relocate_string(index, "新しい訳文")     # STRG のオフセット表を書き換えて再配置
    P.save("data.win")
"""
import struct

from gmdata import Data


class Packer(Data):
    def __init__(self, path):
        super().__init__(path)
        self.buf = bytearray(self.d)
        # AUDO は最終チャンク。その中身の直後から追記領域にする
        audo_off, audo_len = self.chunks["AUDO"]
        assert audo_off + audo_len == len(self.d), "AUDO が最終チャンクではない"
        assert self.u32(audo_off) == 0, "AUDO に音声データが入っている"
        self._audo_off = audo_off
        self._audo_orig_len = audo_len
        self._strg_offsets = self.pointer_list("STRG")

    # ---- 追記領域への確保 ----
    def alloc(self, data, align=4):
        while len(self.buf) % align:
            self.buf.append(0)
        addr = len(self.buf)
        self.buf.extend(data)
        return addr

    def alloc_string(self, text):
        """GM 文字列を確保し、char データ先頭の絶対位置を返す（長さは -4 の位置）"""
        enc = text.encode("utf-8")
        addr = self.alloc(struct.pack("<I", len(enc)) + enc + b"\0")
        return addr + 4

    # ---- 既存チャンクの同サイズ書き換え ----
    def set_pointer_list(self, chunk, ptrs):
        """チャンク先頭の count + ポインタ表を差し替える（元のチャンク長を超えないこと）"""
        off, length = self.chunks[chunk]
        need = 4 + len(ptrs) * 4
        if need > length:
            raise RuntimeError(f"{chunk}: ポインタ表 {need}B が元の {length}B に収まらない")
        struct.pack_into("<I", self.buf, off, len(ptrs))
        struct.pack_into("<%dI" % len(ptrs), self.buf, off + 4, *ptrs)

    def patch(self, addr, data):
        self.buf[addr:addr + len(data)] = data

    def relocate_string(self, index, text):
        """STRG の index 番の文字列を追記領域へ移し、オフセット表を書き換える。

        文字列を**インデックスで**参照している CODE からは透過的。
        名前として絶対ポインタで参照されている文字列には使えない。
        """
        addr = self.alloc_string(text)
        off, _ = self.chunks["STRG"]
        struct.pack_into("<I", self.buf, off + 4 + index * 4, addr - 4)
        self._strg_offsets[index] = addr - 4
        return addr

    def append_string(self, text):
        """STRG に文字列を1件追加し、その新しいインデックスを返す。

        オフセット表が4バイト伸びるが、表の直後にあった元の文字列データは
        全件を追記領域に移してから書き直すので問題ない。
        """
        raise NotImplementedError("必要になったら実装する（現状は relocate で足りる）")

    # ---- 保存 ----
    def save(self, path):
        added = len(self.buf) - len(self.d)
        # AUDO のチャンク長と FORM のサイズを伸ばした分だけ増やす
        struct.pack_into("<I", self.buf, self._audo_off - 4, self._audo_orig_len + added)
        struct.pack_into("<I", self.buf, 4, struct.unpack_from("<I", self.d, 4)[0] + added)
        with open(path, "wb") as f:
            f.write(self.buf)
        return added

    # ---- 検証 ----
    def verify(self):
        """FORM とチャンク境界の整合を確認する"""
        form = struct.unpack_from("<I", self.buf, 4)[0]
        assert 8 + form == len(self.buf), f"FORM サイズ不整合: {8+form} != {len(self.buf)}"
        pos, seen = 8, []
        while pos < 8 + form:
            name = bytes(self.buf[pos:pos + 4]).decode("latin1")
            ln = struct.unpack_from("<I", self.buf, pos + 4)[0]
            seen.append((name, ln))
            pos += 8 + ln
        assert pos == 8 + form, "チャンク境界がFORM末尾で揃わない"
        for name, ln in seen[:-1]:
            assert (name, ln) == (name, self.chunks[name][1]), f"{name} の長さが変わっている"
        return seen
