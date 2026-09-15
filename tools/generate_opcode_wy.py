#!/usr/bin/env python3
"""Regenerate wy/wyrm/opcodes.wy from the opcode table.

`wypoc/compiler_bc/opcodes.py` is the single source of truth for the v1
instruction set (doc/llm-bytecode.md section 3).  This mirrors
`tools/generate_opcode_header.py`, which generates the C VM's
`wyrm/opcode.h` enum from the same `OPS` table; this script generates the
wyrm-source equivalent that `wy/wyrm/image.wy` needs: the data table (with
the `fmt`/`operands`/`wide_fmt`/`wide_operands` disassembly data), `lookup`,
the register-reference helpers, `pack`, and the generated decoder /
disassembler (`unpack`, `disassemble_one`, `disassemble`, `_decode_operand`,
`_signed`, `_render_fmt`, `_fmt_return`) - epic 9 M2 deferred a disassembler
and M4 exercises it via `image.wy`'s `.wy_a`/`.c` writers (vm_plan/epic_9.md).

    pypoc/.venv/bin/python pypoc/tools/generate_opcode_wy.py

test_compiler_bc_format.py (or its wy-format sibling) asserts the checked-in
file still matches, so a change to the table that skips this script fails
the suite rather than shipping a stale opcodes.wy.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wypoc.compiler_bc import opcodes  # noqa: E402

# wy/wyrm/opcodes.wy lives at the repo root, two levels above pypoc/ itself
# (pypoc/tools/generate_opcode_wy.py -> pypoc/ -> repo root).
PYPOC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(PYPOC_ROOT)
WY_TABLE_PATH = os.path.join(REPO_ROOT, "wy", "wyrm", "opcodes.wy")


def _wy_str(text: str) -> str:
    """A wyrm double-quoted string literal for `text` (escaping is the same
    small set doc/language-spec.md defines: backslash and double quote)."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _wy_operands(operands: dict) -> str:
    """A wyrm dict literal for an op's `operands` mapping.

    Python stores `{label: (slot, kind)}` tuples; wyrm has no tuples in the
    table format, so each value is emitted as a 2-element list
    `["a0", "reg"]` that the disassembler reads with `e[0]`/`e[1]`.
    """
    if not operands:
        return "{}"
    fields = [f"{_wy_str(label)}: [{_wy_str(slot)}, {_wy_str(kind)}]" for label, (slot, kind) in operands.items()]
    return "{" + ", ".join(fields) + "}"


def _wy_fmt(fmt) -> str:
    """A wyrm string literal for an op's `fmt` template.

    `fmt` is either a format string or (for the `return` op) a callable;
    the callable is rendered by the generated `_fmt_return` instead, so its
    template slot is emitted empty.
    """
    if not isinstance(fmt, str):
        return '""'
    return _wy_str(fmt)


def _op_record(op: opcodes.Op) -> str:
    fields = [
        f'"name": {_wy_str(op.name)}',
        f'"value": 0x{op.value:02X}',
        f'"form": {_wy_str(op.form)}',
    ]
    if op.form == opcodes.PAIRABLE:
        fields.append(f'"shape": {_wy_str(op.shape)}')
        fields.append(f'"wide_name": {_wy_str(op.wide_name)}')
        fields.append(f'"wide_value": 0x{op.wide_value:02X}')
    else:
        fields.append('"shape": ""')
        fields.append('"wide_name": ""')
        fields.append('"wide_value": 0')
    fields.append(f'"fmt": {_wy_fmt(op.fmt)}')
    fields.append(f'"operands": {_wy_operands(op.operands)}')
    fields.append(f'"wide_fmt": {_wy_fmt(op.wide_fmt)}')
    fields.append(f'"wide_operands": {_wy_operands(op.wide_operands)}')
    return "    {" + ", ".join(fields) + "}"


# The wyrm decoder/disassembler, emitted verbatim into opcodes.wy after the
# table and pack().  It is a hand-written port of opcodes.py's unpack /
# disassemble / disassemble_one / _decode_operand / _signed (opcodes.py's
# annotation hints are split out into image.wy, which owns the image pools;
# _render_fmt/_fmt_return replace Python's str.format and the return op's
# callable fmt).  Written without backslash escapes and without str methods
# (the C VM's builtins message table has no `str` overloads, so `s ! substr`
# faults; `s[i]` yields a codepoint int and `_char_from_cp` rebuilds text) -
# '{'/'}' are matched as codepoints 123/125, so the Python triple-quote
# below needs no escaping.
DISASM_BLOCK = r"""
# --------------------------------------------------------------------
# decoding and disassembly

DIGITS := "0123456789ABCDEF"

# UTF-8 encode one codepoint (mirrors decode.wy's _encode_codepoint - the
# C VM has no chr()-style builtin).
fn _char_from_cp(cp: int) -> str:
    if cp < 0x80:
        b := bytes(1)
        b!pack_u8(0, cp)
        return b!to_str()
    elif cp < 0x800:
        b := bytes(2)
        b!pack_u8(0, 0xC0 | (cp >> 6))
        b!pack_u8(1, 0x80 | (cp & 0x3F))
        return b!to_str()
    elif cp < 0x10000:
        b := bytes(3)
        b!pack_u8(0, 0xE0 | (cp >> 12))
        b!pack_u8(1, 0x80 | ((cp >> 6) & 0x3F))
        b!pack_u8(2, 0x80 | (cp & 0x3F))
        return b!to_str()
    else:
        b := bytes(4)
        b!pack_u8(0, 0xF0 | (cp >> 18))
        b!pack_u8(1, 0x80 | ((cp >> 12) & 0x3F))
        b!pack_u8(2, 0x80 | ((cp >> 6) & 0x3F))
        b!pack_u8(3, 0x80 | (cp & 0x3F))
        return b!to_str()

fn _hex_digits(value: int, min_digits: int) -> str:
    out := ""
    n := value
    while n > 0 or len(out) < min_digits:
        d := n & 0xF
        out = _char_from_cp(DIGITS[d]) + out
        n = n >> 4
    return out

fn _hex2(value: int) -> str:
    return _hex_digits(value & 0xFF, 2)

# Decode the instruction at words[offset]. Returns
#   { "entry": dict, "is_wide": bool, "fields": dict, "nwords": int }
# where fields carries the raw slot values op/f/a0/a1/a2/w1 (mirrors
# opcodes.py's unpack).
fn unpack(words: list, offset: int = 0) -> dict | error:
    word0 := words[offset]
    value := word0 & 0xFF
    entry := BY_VALUE[value] catch nil
    if entry is nil:
        return error("unknown opcode 0x" + _hex2(value) + " at word " + str(offset))
    fields := {"op": value, "f": (word0 >> 8) & 0xFF, "a0": (word0 >> 16) & 0xFFFF, "a1": 0, "a2": 0, "w1": 0}
    if value < LONG_START:
        return {"entry": entry, "is_wide": false, "fields": fields, "nwords": 1}
    if offset + 1 >= len(words):
        return error("truncated two-word instruction at word " + str(offset))
    word1 := words[offset + 1]
    fields["w1"] = word1
    fields["a1"] = (word1 >> 16) & 0xFFFF
    fields["a2"] = word1 & 0xFFFF
    return {"entry": entry, "is_wide": entry["form"] == PAIRABLE, "fields": fields, "nwords": 2}

fn _signed(raw: int, bits: int) -> int:
    sign := 1 << (bits - 1)
    return (raw & (sign - 1)) - (raw & sign)

# rel operands print with a leading + for non-negative values (Python's
# f"{signed:+d}"), and probably signed when 32-bit.
fn _plus_signed(value: int) -> str:
    if value >= 0:
        return "+" + str(value)
    return str(value)

# Render an f32 operand.  The bits ride in a u32 word; shift each byte out
# (no 0xFFFFFFFF mask - pypoc rejects that literal as exceeding i32, and the
# bit-and already truncates) into a 4-byte buffer, then bytes!unpack_f32.
fn _f32_text(raw: int) -> str:
    b := bytes(4)
    b!pack_u8(0, raw & 0xFF)
    b!pack_u8(1, (raw >> 8) & 0xFF)
    b!pack_u8(2, (raw >> 16) & 0xFF)
    b!pack_u8(3, (raw >> 24) & 0xFF)
    value := b!unpack_f32(0)
    return str(value)

# Render a raw operand to its listing text (opcodes.py's _decode_operand,
# with the int-like _RenderedInt results replaced by plain str).
fn _decode_operand(raw: int, kind: str) -> str:
    if kind == "reg":
        return reg_name(raw)
    if kind == "reg8":
        return reg_name(from_reg8(raw))
    if kind == "global":
        return "g" + str(raw)
    if kind == "static":
        return "static#" + str(raw)
    if kind == "symbol":
        return "sym#" + str(raw)
    if kind == "message":
        return "msg#" + str(raw)
    if kind == "function":
        return "fn#" + str(raw)
    if kind == "class":
        return "class#" + str(raw)
    if kind == "rel":
        var signed: int
        if raw <= 0xFFFF:
            signed = _signed(raw, 16)
        else:
            signed = _signed(raw, 32)
        return _plus_signed(signed)
    if kind == "i8":
        return str(_signed(raw, 8))
    if kind == "i32":
        return str(_signed(raw, 32))
    if kind == "f32":
        return _f32_text(raw)
    return str(raw)

# Python's fmt.format(**values) replaced by a one-pass template scan:
# every {label} pulls value[label], everything else carries through.  The
# table's only callable fmt is the return op, handled by _fmt_return.
fn _render_fmt(fmt: str, values: dict) -> str | error:
    out := ""
    i := 0
    n := len(fmt)
    while i < n:
        if fmt[i] != 123:
            out = out + _char_from_cp(fmt[i])
            i = i + 1
        else:
            j := i + 1
            label := ""
            while j < n and fmt[j] != 125:
                label = label + _char_from_cp(fmt[j])
                j = j + 1
            if j >= n:
                return error("unterminated { in fmt " + fmt)
            value := values[label] catch nil
            if value is nil:
                return error("unknown fmt label {" + label + "}")
            out = out + value
            i = j + 1
    return out

# The appendix spells a zero-value return without a base register, since
# the base is meaningless when nothing is returned (opcodes.py's
# _render_return).
fn _fmt_return(fields: dict) -> str:
    if fields["f"] == 0:
        return "return count=0"
    return "return " + _decode_operand(fields["a0"], "reg") + " count=" + str(fields["f"])

# Render the instruction at words[offset].  Returns a record dict:
#   { "offset": int, "text": str, "nwords": int, "is_wide": bool,
#     "entry": dict, "fields": dict, "operands": dict }
# carrying everything image.wy needs to append its (note) annotation hints
# (which render from fields + operands, so the disassembler stays image-free).
fn disassemble_one(words: list, offset: int = 0) -> dict | error:
    packed := unpack(words, offset)
    if packed is error:
        return packed
    entry := packed["entry"]
    is_wide := packed["is_wide"]
    fields := packed["fields"]
    var operands: dict
    var fmt: str
    if is_wide:
        operands = entry["wide_operands"]
    else:
        operands = entry["operands"]
    if is_wide and entry["wide_fmt"] != "":
        fmt = entry["wide_fmt"]
    else:
        fmt = entry["fmt"]
    var text: str
    if entry["name"] == "return":
        text = _fmt_return(fields)
    else:
        values := {}
        for label in operands:
            e := operands[label]
            values[label] = _decode_operand(fields[e[0]], e[1])
        rendered := _render_fmt(fmt, values)
        if rendered is error:
            return rendered
        text = rendered
    return {"offset": offset, "text": text, "nwords": packed["nwords"], "is_wide": is_wide, "entry": entry, "fields": fields, "operands": operands}

# Render a whole code array as disassembly records, word offsets shifted by
# start_word (mirrors opcodes.py's disassemble).
fn disassemble(words: list, start_word: int = 0) -> list | error:
    out := []
    offset := 0
    while offset < len(words):
        rec := disassemble_one(words, offset)
        if rec is error:
            return rec
        rec["offset"] = start_word + offset
        out!append(rec)
        offset = offset + rec["nwords"]
    return out
"""


def opcode_wy() -> str:
    """The `wy/wyrm/opcodes.wy` this table implies.

    The data table carries everything `opcodes.py`'s `OPS` rows do: name,
    value, form, the pairable twin's shape/wide_name/wide_value, and the
    disassembly data (`fmt`/`operands`/`wide_fmt`/`wide_operands`, with an
    empty string for `return`'s callable fmt - `_fmt_return` renders it by
    name).  Around the table sit `lookup`, the register-reference helpers,
    `pack`, and the generated decoder/disassembler.  Not included:
    `pack_pairable`'s automatic compact/wide fallback (epic 9's hand
    assembly already knows which form each instruction uses), and the
    image-dependent `(note)` annotation hints - those live in image.wy's
    `_annotate`, which reads each disassembly record's `fields`/`operands`.
    """
    out = [
        "# wyrm bytecode opcodes - GENERATED from wypoc/compiler_bc/opcodes.py.",
        "#",
        "# Do not hand-edit: run pypoc/tools/generate_opcode_wy.py after changing",
        "# the opcode table. doc/llm-bytecode.md section 3 is the prose alongside",
        "# it.",
        "#",
        "# Instruction encoding (section 2), little-endian:",
        "#",
        "#   word 0:  [ a0 : u16 ][ f : u8 ][ op : u8 ]",
        "#   word 1:  [ a1 : u16 ][ a2 : u16 ]",
        "#",
        "# Bit 7 of the opcode is the length: op < 0x80 is one word, op >= 0x80",
        "# is two words.",
        "#",
        "# This module is opcodes.py's full wyrm port: the data table (including",
        "# the fmt/operands/wide_fmt/wide_operands disassembly data), lookup, the",
        "# register-reference helpers, pack, and a generated decoder/disassembler",
        "# (unpack, disassemble_one, disassemble, _decode_operand, _signed,",
        "# _render_fmt, _fmt_return).  The image-dependent '(note)' annotation",
        "# hints are applied by image.wy (opcodes.py annotates inline; this port",
        "# splits that out to keep the generated module free of image knowledge).",
        "# pack_pairable's automatic compact/wide fallback is not included - epic",
        "# 9's hand assembly already knows which form each instruction uses.",
        "",
        f"LONG_START := 0x{opcodes.LONG_START:02X}",
        f"P_BIT := 0x{opcodes.P_BIT:04X}",
        "",
        f'CORE := {_wy_str(opcodes.CORE)}',
        f'PAIRABLE := {_wy_str(opcodes.PAIRABLE)}',
        f'LONG := {_wy_str(opcodes.LONG)}',
        "",
        "# Every row carries name/value/form and the disassembly data; pairable",
        "# rows additionally carry shape/wide_name/wide_value (the wide twin's",
        '# mnemonic and value, or ""/0 on a row that has none). "fmt" is the',
        '# listing template over "operands" (label -> [slot, kind]); the wide',
        '# twin\'s own template/operands ride in "wide_fmt"/"wide_operands"',
        '# (""/{} when it shares the compact form). `return`\'s fmt is emitted',
        '# "" because it is a callable in opcodes.py - `_fmt_return` renders it.',
        "OPS := [",
    ]
    records = [_op_record(op) for op in sorted(opcodes.OPS, key=lambda entry: entry.value)]
    for i, record in enumerate(records):
        out.append(record + ("," if i < len(records) - 1 else ""))
    out.append("]")
    out.append("")
    out.extend(
        [
            "# BY_NAME/BY_VALUE index OPS by mnemonic (str) and by opcode value",
            "# (int), the wide twin of a pairable op included in both, exactly",
            "# like opcodes.py's BY_NAME/BY_VALUE.",
            "fn _build_index():",
            "    by_name := {}",
            "    by_value := {}",
            "    for op in OPS:",
            '        by_name[op["name"]] = op',
            '        by_value[op["value"]] = op',
            '        if op["form"] == PAIRABLE:',
            '            by_value[op["wide_value"]] = op',
            '            if op["wide_name"] != op["name"]:',
            '                by_name[op["wide_name"]] = op',
            "    return [by_name, by_value]",
            "",
            "_INDICES := _build_index()",
            "BY_NAME := _INDICES[0]",
            "BY_VALUE := _INDICES[1]",
            "",
            "# `op` is a mnemonic (sym) or an opcode value (int); resolve it to its",
            "# table entry (a dict, see OPS above).",
            "fn lookup(op: sym | int) -> dict:",
            "    var entry: dict | nil",
            "    if op is sym:",
            "        entry = BY_NAME[str(op)] catch nil",
            "    else:",
            "        entry = BY_VALUE[op] catch nil",
            "    if entry is nil:",
            '        return error("unknown opcode " + str(op))',
            "    return entry",
            "",
            "# --------------------------------------------------------------------",
            "# register references (spec 2.1)",
            "",
            "# A u16 register reference naming L`n` (a local or temp). ('slot' is a",
            "# reserved word in wyrm's own grammar, so the parameter is named `n`.)",
            "fn L(n: int) -> int:",
            "    if n < 0 or n > 0x7FFF:",
            '        return error("L" + str(n) + " is outside the 32767-local frame limit")',
            "    return n",
            "",
            "# A u16 register reference naming P`n` (a this value, param or capture).",
            "fn P(n: int) -> int:",
            "    if n < 0 or n > 0x7FFF:",
            '        return error("P" + str(n) + " is outside the 32767-slot P frame limit")',
            "    return P_BIT | n",
            "",
            "fn is_p(reg: int) -> bool:",
            "    return (reg & P_BIT) != 0",
            "",
            "fn reg_index(reg: int) -> int:",
            "    return reg & 0x7FFF",
            "",
            "fn reg_name(reg: int) -> str:",
            '    if is_p(reg):',
            '        return "P" + str(reg_index(reg))',
            '    return "L" + str(reg_index(reg))',
            "",
            "# The 8-bit form of a register reference, or nil when it does not fit.",
            "#",
            "# A reg8 addresses L0-L127 (bit 7 clear) or P0-P127 (bit 7 set);",
            "# anything above that forces the wide form of the instruction.",
            "fn to_reg8(reg: int) -> int | nil:",
            "    index := reg_index(reg)",
            "    if index > 127:",
            "        return nil",
            "    if is_p(reg):",
            "        return 0x80 | index",
            "    return index",
            "",
            "fn from_reg8(byte: int) -> int:",
            "    if (byte & 0x80) != 0:",
            "        return P(byte & 0x7F)",
            "    return L(byte)",
            "",
            "fn fits_i8(value: int) -> bool:",
            "    return value >= -128 and value <= 127",
            "",
            "fn fits_i16(value: int) -> bool:",
            "    return value >= -32768 and value <= 32767",
            "",
            "# --------------------------------------------------------------------",
            "# encoding",
            "",
            "fn _check_field(name: str, value: int, low: int, high: int) -> nil:",
            "    if value < low or value > high:",
            "        return error(",
            '            "instruction field " + name + "=" + str(value) +'
            ' " is outside " + str(low) + ".." + str(high)',
            "        )",
            "    return nil",
            "",
            "fn _value_for_name(entry: dict, name) -> int:",
            "    name_str := str(name)",
            '    if entry["form"] == PAIRABLE and name_str == entry["wide_name"]'
            ' and entry["wide_name"] != entry["name"]:',
            '        return entry["wide_value"]',
            '    return entry["value"]',
            "",
            "# Encode one instruction into a list of 1 or 2 u32 words.",
            "#",
            "# `op` is a mnemonic or an opcode value; for a pairable op the mnemonic",
            "# selects the compact form, so callers that want the wide form pass its",
            "# value (compact | 0x80) or - for i8/i32 - the wide mnemonic. `w1` sets",
            "# the whole second word (a 32-bit payload) and is mutually exclusive",
            "# with a1/a2. Unlike opcodes.py's pack(), the words below are not",
            "# masked with 0xFFFFFFFF: that literal does not fit wyrm's 32-bit",
            "# `int`, and it is unnecessary - shifting/OR-ing already-range-checked",
            "# fields, or passing w1 straight through, produces the same 32-bit bit",
            "# pattern either way; only the printed sign differs, which the VM does",
            "# not care about.",
            "fn pack(op, f: int = 0, a0: int = 0, a1: int = 0, a2: int = 0, w1: int | nil = nil) -> list:",
            "    entry := lookup(op)",
            "    var value: int",
            "    if op is int:",
            "        value = op",
            "    else:",
            "        value = _value_for_name(entry, op)",
            '    _check_field("op", value, 0, 0xFF)',
            '    _check_field("f", f, 0, 0xFF)',
            '    _check_field("a0", a0, 0, 0xFFFF)',
            "    word0 := ((a0 & 0xFFFF) << 16) | ((f & 0xFF) << 8) | value",
            "    if value < LONG_START:",
            "        if a1 != 0 or a2 != 0 or w1 is not nil:",
            '            return error(entry["name"] + ": one-word form has no second word")',
            "        return [word0]",
            "    var word1: int",
            "    if w1 is not nil:",
            "        if a1 != 0 or a2 != 0:",
            '            return error(entry["name"] + ": w1 and a1/a2 are alternatives")',
            "        word1 = w1",
            "    else:",
            '        _check_field("a1", a1, 0, 0xFFFF)',
            '        _check_field("a2", a2, 0, 0xFFFF)',
            "        word1 = ((a1 & 0xFFFF) << 16) | (a2 & 0xFFFF)",
            "    return [word0, word1]",
            "",
        ]
    )
    out.append("")
    out.extend(DISASM_BLOCK.splitlines())
    out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    generated = opcode_wy()
    existing = None
    if os.path.exists(WY_TABLE_PATH):
        with open(WY_TABLE_PATH) as f:
            existing = f.read()
    if existing == generated:
        print(f"{WY_TABLE_PATH}: already up to date")
        return 0
    with open(WY_TABLE_PATH, "w") as f:
        f.write(generated)
    print(f"{WY_TABLE_PATH}: regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
