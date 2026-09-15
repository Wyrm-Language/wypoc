"""bytes: construction, indexing, and every message doc/stdlib.md's
`### bytes` section lists (append/resize/slice/to_str/copy, the eight
pack_*/unpack_* natives), plus ==, `is bytes`, and str()'s rendering.

Follows test_eval.py's style: `run(src)` evaluates a snippet through the
tree-walking interpreter and hands back its top-level variables."""
import struct

import pytest

from wypoc import wyrm_builtins
from wypoc.compiler_bc import bsonlite
from wypoc.parse import parse
from wypoc.wyrm_eval_parse_tree import Variable, eval_program


def run(src: str) -> dict:
    ctx: dict = {}
    wyrm_builtins.install(ctx)
    eval_program(parse(src if src.endswith("\n") else src + "\n"), ctx)
    return {name: var.value for name, var in ctx.items()
            if isinstance(var, Variable)}


# --------------------------------------------------------------------------
# construction


def test_bytes_from_int_is_that_many_zero_bytes():
    b = run("b := bytes(3)")["b"]
    assert isinstance(b, bytearray)
    assert bytes(b) == b"\x00\x00\x00"


def test_bytes_from_str_is_utf8_encoded():
    b = run('b := bytes("hié")')["b"]
    assert bytes(b) == "hié".encode("utf-8")


def test_bytes_from_bytes_is_a_copy():
    values = run("a := bytes(2)\na!pack_u8(0, 9)\nb := bytes(a)\nb!pack_u8(0, 1)")
    a, b = values["a"], values["b"]
    assert bytes(a) == b"\x09\x00"
    assert bytes(b) == b"\x01\x00"
    assert a is not b


def test_len_builtin_counts_bytes():
    values = run('b := bytes("hi")\nn := len(b)\n')
    assert values["n"] == 2
    assert values["n"] == len(values["b"])


# --------------------------------------------------------------------------
# indexing


def test_index_get_returns_int_0_255():
    values = run('b := bytes("A")')
    b = values["b"]
    assert b[0] == 65


def test_index_get_out_of_range_is_an_error_value_not_a_crash():
    from wypoc.wyrm_builtins import is_error

    values = run("b := bytes(1)\nx := b[5]")
    assert is_error(values["x"])


def test_index_set_writes_a_byte_and_range_checks_the_value():
    values = run("b := bytes(2)\nb[0] = 200")
    assert values["b"][0] == 200

    with pytest.raises(ValueError):
        run("b := bytes(2)\nb[0] = 300")


def test_index_set_range_checks_the_index():
    with pytest.raises(IndexError):
        run("b := bytes(2)\nb[9] = 1")


# --------------------------------------------------------------------------
# messages


def test_append_int_bytes_and_str():
    values = run(
        'b := bytes(0)\n'
        'b!append(65)\n'
        'b!append(bytes("Z"))\n'
        'b!append("q")\n'
    )
    assert bytes(values["b"]) == b"AZq"


def test_append_rejects_out_of_range_int():
    with pytest.raises(ValueError):
        run("b := bytes(0)\nb!append(256)")


def test_resize_pads_with_zero_and_truncates():
    values = run("b := bytes(1)\nb!pack_u8(0, 7)\nb!resize(3)")
    assert bytes(values["b"]) == b"\x07\x00\x00"

    values = run("b := bytes(3)\nb!pack_u8(0, 7)\nb!resize(1)")
    assert bytes(values["b"]) == b"\x07"


def test_slice_copies_a_range_and_range_checks():
    values = run('b := bytes("hello")\ns := b!slice(1, 3)')
    assert bytes(values["s"]) == b"ell"

    with pytest.raises(IndexError):
        run('b := bytes("hi")\nb!slice(1, 5)')


def test_copy_makes_an_independent_bytes():
    values = run("a := bytes(1)\na!pack_u8(0, 1)\nb := a!copy()\nb!pack_u8(0, 2)")
    assert bytes(values["a"]) == b"\x01"
    assert bytes(values["b"]) == b"\x02"
    assert values["a"] is not values["b"]


def test_to_str_round_trips_utf8():
    values = run('b := bytes("café")\ns := b!to_str()')
    assert values["s"] == "café"


def test_to_str_faults_on_invalid_utf8():
    with pytest.raises(UnicodeDecodeError):
        run("b := bytes(1)\nb!pack_u8(0, 255)\nb!to_str()")


# --------------------------------------------------------------------------
# pack_*/unpack_*: exact byte-value assertions, one width at a time, plus a
# bsonlite-equivalence check.


def test_pack_u8_and_unpack_u8():
    values = run("b := bytes(1)\nb!pack_u8(0, 200)\nx := b!unpack_u8(0)")
    assert bytes(values["b"]) == b"\xc8"
    assert values["x"] == 200


def test_pack_i32_and_unpack_i32_are_little_endian_twos_complement():
    values = run("b := bytes(4)\nb!pack_i32(0, -1)\nx := b!unpack_i32(0)")
    assert bytes(values["b"]) == b"\xff\xff\xff\xff"
    assert values["x"] == -1


def test_pack_u32_matches_the_spec_example():
    # doc/stdlib.md's own worked example: 258 = 0x00000102 -> 02 01 00 00.
    values = run("b := bytes(4)\nb!pack_u32(0, 258)\nx := b!unpack_u32(0)")
    assert bytes(values["b"]) == b"\x02\x01\x00\x00"
    assert values["x"] == 258


def test_pack_u32_range_check_faults_like_the_spec_example():
    # doc/stdlib.md: bytes(3)!pack_u32(0, 258) needs bytes 0..3 inclusive
    # (4 bytes) but the buffer is only 3 long - faults.
    with pytest.raises(IndexError):
        run("b := bytes(3)\nb!pack_u32(0, 258)")


def test_pack_f32_and_unpack_f32():
    values = run("b := bytes(4)\nb!pack_f32(0, 1.5)\nx := b!unpack_f32(0)")
    assert bytes(values["b"]) == struct.pack("<f", 1.5)
    assert values["x"] == 1.5


def test_pack_f64_and_unpack_f64():
    values = run("b := bytes(8)\nb!pack_f64(0, 1.5)\nx := b!unpack_f64(0)")
    assert bytes(values["b"]) == struct.pack("<d", 1.5)
    assert values["x"] == 1.5


def test_pack_i32_matches_bsonlites_own_int32_encoding():
    """bsonlite (compiler_bc/bsonlite.py) encodes a plain int as TAG_INT32 +
    a little-endian 4-byte payload via `struct.pack("<i", value)` - the same
    encoding pack_i32 must produce, so a compiled image's static pool and a
    runtime-built bytes value agree on the wire format for the same int."""
    value = -12345
    values = run(f"b := bytes(4)\nb!pack_i32(0, {value})")
    tag, payload = bsonlite._value(value)
    assert tag == bsonlite.TAG_INT32
    assert bytes(values["b"]) == payload


def test_pack_f64_matches_bsonlites_own_double_encoding():
    value = 3.5
    values = run(f"b := bytes(8)\nb!pack_f64(0, {value})")
    tag, payload = bsonlite._value(value)
    assert tag == bsonlite.TAG_DOUBLE
    assert bytes(values["b"]) == payload


def test_unpack_range_check_faults():
    with pytest.raises(IndexError):
        run("b := bytes(2)\nb!unpack_i32(0)")


# --------------------------------------------------------------------------
# ==, `is bytes`, str()


def test_equality_is_byte_for_byte():
    values = run('a := bytes("ab")\nb := bytes("ab")\nc := bytes("ac")\n'
                 'eq := a == b\nne := a == c\n')
    assert values["eq"] is True
    assert values["ne"] is False


def test_is_bytes_matches_only_bytes():
    values = run('a := bytes(1)\nx := a is bytes\ny := "a" is bytes\n')
    assert values["x"] is True
    assert values["y"] is False


def test_str_of_bytes_renders_as_n_bytes():
    values = run('s := str(bytes(3))')
    assert values["s"] == "3 bytes"


def test_str_of_bytes_matches_the_static_pool_disassembly_format():
    """str(b) is documented (doc/stdlib.md) to mirror the length-only
    summary compiler_bc/image.py's `_static_repr` already prints for a
    binary static-pool constant - same format, not a second one."""
    from wypoc.compiler_bc.image import _static_repr

    b = run("b := bytes(5)")["b"]
    assert wyrm_builtins._to_str(b) == _static_repr(bytes(b))
