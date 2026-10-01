"""Scheme s-expression printing of wyrm values (the D2 form).

The one printer for pair lists. Tree dumps (`wyrm --dump-ast`), `str()`,
`print`/`println` and the REPL all render a pair list through `write`, so a
tree printed anywhere reads the same:

    proper pair list   (a b c); the empty list, nil, is ()
    improper pair      (a . b)
    symbol             its bare spelling: define, +, ::, $ast
    str                "...", with \\\\ \\" \\n \\r \\t escaped and any other
                        control character as \\xHH
    int                decimal
    float              shortest round-trip form, always with a `.` or an
                        exponent: 1.0, 1.5e3

`write` is for a whole value. A pair list's elements that aren't wire data
(an array, a dict, a class instance) are handed to `other`, which by default
is the language's own repr-mode rendering (see wyrm_builtins._repr_str).
"""
from wypoc.wyrm_builtins import ELLIPSIS, NIL, Pair, Symbol

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def quote(text: str) -> str:
    """`text` as a D2 string: double-quoted, escaped per the module
    docstring."""
    out = ['"']
    for ch in text:
        escaped = _ESCAPES.get(ch)
        if escaped is not None:
            out.append(escaped)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02X}")
        elif 0xDC80 <= ord(ch) <= 0xDCFF:
            # A byte that wasn't valid UTF-8, carried as a surrogate escape.
            out.append(f"\\x{ord(ch) - 0xDC00:02X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def spell_float(value: float) -> str:
    """Shortest round-trip spelling, always with a `.` or an exponent.
    Python's `repr` already is that, apart from spelling the exponent
    `e+20` where D2 writes `e20`."""
    text = repr(value)
    if "e" in text:
        mantissa, exponent = text.split("e")
        return f"{mantissa}e{int(exponent)}"
    return text


def is_wire_atom(value) -> bool:
    """Whether `value` is one of the atoms D2 has a spelling for."""
    return (value is None or value is NIL or isinstance(value, (Pair, Symbol, str))
            or (isinstance(value, (int, float)) and not isinstance(value, bool)))


def write(value, other=None) -> str:
    """`value` in D2 form, on one line. `other(v)` renders a value that
    has no D2 spelling (see the module docstring)."""
    if value is None or value is NIL:
        return "()"
    if isinstance(value, Pair):
        parts = []
        cursor = value
        while isinstance(cursor, Pair):
            parts.append(write(cursor.car, other))
            cursor = cursor.cdr
        if cursor is not NIL and cursor is not None:
            parts += [".", write(cursor, other)]
        return "(" + " ".join(parts) + ")"
    if isinstance(value, Symbol):
        return value.name
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return spell_float(value)
    if value is ELLIPSIS:
        return "..."
    if other is not None:
        return other(value)
    from wypoc.wyrm_builtins import _repr_str
    return _repr_str(value)
