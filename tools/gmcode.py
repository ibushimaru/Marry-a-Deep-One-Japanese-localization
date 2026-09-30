"""GameMaker Studio 1.4 (bytecode 16) CODE チャンクの逆アセンブラ（読み取り専用）。

命令は4バイト固定 + 型に応じたオペランド。
変数名・関数名は VARI/FUNC の「出現位置チェーン」を辿って解決する。
"""
import struct
from gmdata import Data

# bytecode 15/16 のオペコード
OPS = {
    0x07: "conv", 0x08: "mul", 0x09: "div", 0x0A: "rem", 0x0B: "mod",
    0x0C: "add", 0x0D: "sub", 0x0E: "and", 0x0F: "or", 0x10: "xor",
    0x11: "neg", 0x12: "not", 0x13: "shl", 0x14: "shr", 0x15: "cmp",
    0x45: "pop", 0x86: "dup", 0x9C: "ret", 0x9D: "exit", 0x9E: "popz",
    0xB6: "b", 0xB7: "bt", 0xB8: "bf", 0xBA: "pushenv", 0xBB: "popenv",
    0xC0: "push", 0xC1: "push.loc", 0xC2: "push.glb", 0xC3: "push.bltn",
    0x84: "pushi", 0xD9: "call", 0x99: "callv", 0xFF: "break",
}
PUSH_OPS = {0xC0, 0xC1, 0xC2, 0xC3, 0x84}
GOTO_OPS = {0xB6, 0xB7, 0xB8, 0xBA, 0xBB}
TYPES = {0x00: "double", 0x01: "float", 0x02: "int32", 0x03: "int64",
         0x04: "bool", 0x05: "var", 0x06: "str", 0x07: "inst", 0x0F: "int16"}
OPERAND_SIZE = {0x00: 8, 0x03: 8, 0x01: 4, 0x02: 4, 0x04: 4, 0x05: 4,
                0x06: 4, 0x07: 4, 0x0F: 0}
CMP = {1: "<", 2: "<=", 3: "==", 4: "!=", 5: ">=", 6: ">"}
INST = {-1: "self", -2: "other", -3: "all", -4: "noone", -5: "global",
        -7: "local", -6: "builtin"}


class Code(Data):
    def __init__(self, path):
        super().__init__(path)
        self._entries = None
        self._funcmap = None
        self._varmap = None

    # ---- CODE エントリ ----
    def entries(self):
        if self._entries is not None:
            return self._entries
        out = []
        for p in self.pointer_list("CODE"):
            name = self.gmstr(self.u32(p))
            length = self.u32(p + 4)
            nlocals, nargs = struct.unpack_from("<2H", self.d, p + 8)
            rel = struct.unpack_from("<i", self.d, p + 12)[0]
            out.append({
                "ptr": p, "name": name, "length": length,
                "locals": nlocals, "args": nargs,
                "start": p + 12 + rel, "end": p + 12 + rel + length,
            })
        self._entries = out
        return out

    def entry_of(self, addr):
        for e in self.entries():
            if e["start"] <= addr < e["end"]:
                return e
        return None

    # ---- 出現位置チェーン ----
    def _chain(self, chunk, stride, name_off, occ_off, first_off):
        o, _ = self.chunks[chunk]
        count = self.u32(o)
        p = o + 4
        m = {}
        for _ in range(count):
            name = self.gmstr(self.u32(p + name_off))
            occ = self.u32(p + occ_off)
            addr = self.u32(p + first_off)
            p += stride
            for _ in range(occ):
                m[addr] = name
                nxt = self.u32(addr + 4) & 0x00FFFFFF
                if nxt == 0:
                    break
                addr += nxt
        return m

    def funcmap(self):
        """命令アドレス -> 呼び出される関数名"""
        if self._funcmap is None:
            self._funcmap = self._chain("FUNC", 12, 0, 4, 8)
        return self._funcmap

    def varmap(self):
        """命令アドレス -> 参照される変数名"""
        if self._varmap is None:
            o, length = self.chunks["VARI"]
            # VARI: count1, count2, maxLocal のあと 20バイトのエントリがチャンク末尾まで並ぶ。
            # 先頭の count1 は全体数ではないので、チャンク長から実際の件数を出す。
            count = (length - 12) // 20
            p = o + 12
            m = {}
            for _ in range(count):
                name = self.gmstr(self.u32(p))
                occ = self.u32(p + 12)
                addr = self.u32(p + 16)
                p += 20
                for _ in range(occ):
                    m[addr] = name
                    nxt = self.u32(addr + 4) & 0x00FFFFFF
                    if nxt == 0:
                        break
                    addr += nxt
            self._varmap = m
        return self._varmap

    # ---- 逆アセンブル ----
    def disasm(self, entry):
        d = self.d
        fm, vm = self.funcmap(), self.varmap()
        strs = [s for _, s in self.strings()]
        a, out = entry["start"], []
        while a < entry["end"]:
            b0, b1, b2, op = d[a], d[a + 1], d[a + 2], d[a + 3]
            mn = OPS.get(op, f"?{op:02X}")
            size, txt = 4, mn

            if op in PUSH_OPS:
                t1 = b2
                osz = OPERAND_SIZE.get(t1, 4)
                size = 4 + osz
                tn = TYPES.get(t1, hex(t1))
                if t1 == 0x0F:
                    txt = f"pushi.i16 {struct.unpack_from('<h', d, a)[0]}"
                elif t1 == 0x06:
                    idx = self.u32(a + 4)
                    s = strs[idx] if idx < len(strs) else "?"
                    txt = f'push.str [{idx}] "{s[:60]}"'
                elif t1 == 0x00:
                    txt = f"push.double {struct.unpack_from('<d', d, a + 4)[0]}"
                elif t1 == 0x02:
                    txt = f"push.int32 {struct.unpack_from('<i', d, a + 4)[0]}"
                elif t1 == 0x05:
                    inst = struct.unpack_from("<h", d, a)[0]
                    nm = vm.get(a, "?")
                    txt = f"{mn}.var {INST.get(inst, inst)}.{nm}"
                else:
                    txt = f"{mn}.{tn}"

            elif op == 0x45:  # pop
                t1, t2 = b2 & 0x0F, b2 >> 4
                if t1 == 0x0F:
                    txt = "pop.swap"
                else:
                    size = 8
                    inst = struct.unpack_from("<h", d, a)[0]
                    txt = f"pop.{TYPES.get(t2,t2)} {INST.get(inst, inst)}.{vm.get(a,'?')}"

            elif op == 0xD9:  # call
                size = 8
                txt = f"call {fm.get(a,'?')}({struct.unpack_from('<H', d, a)[0]})"

            elif op == 0x15:  # cmp
                txt = f"cmp {CMP.get(b1, b1)}"

            elif op in GOTO_OPS:
                off = struct.unpack_from("<i", d[a:a + 4] + b"\0")[0] & 0xFFFFFF
                if off & 0x800000:
                    off -= 0x1000000
                txt = f"{mn} -> {a + off * 4 - entry['start']:+d} (@{a + off * 4 - entry['start']})"

            elif op == 0x07:  # conv
                txt = f"conv.{TYPES.get(b2 & 0x0F, b2 & 0x0F)}->{TYPES.get(b2 >> 4, b2 >> 4)}"

            out.append((a - entry["start"], txt))
            a += size
        return out
