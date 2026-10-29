"""Translate a PowerCenter expression to Oracle SQL.

Rule-based only. Anything without a safe mapping is emitted with an inline
``/* NEEDS_REVIEW */`` marker and recorded in ``Translation.review`` so that it
can never pass through silently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .lexer import ExprSyntaxError, parse

VAL, BOOL = "v", "b"


@dataclass
class Context:
    ref: Callable[[str], str] = lambda name: name
    params: dict[str, str] = field(default_factory=dict)
    lookup_macros: dict[str, str] = field(default_factory=dict)  # lookup name -> macro name
    overrides: dict[str, str] = field(default_factory=dict)  # FUNC -> sql template using {0},{1}..


@dataclass
class Translation:
    sql: str
    review: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lookups: set[str] = field(default_factory=set)

    @property
    def needs_review(self) -> bool:
        return bool(self.review)


_DD = {"DD_INSERT": "0", "DD_UPDATE": "1", "DD_DELETE": "2", "DD_REJECT": "3"}
_FMT_SUBS = [("NS", "FF9"), ("US", "FF6"), ("MS", "FF3")]
_DATE_PARTS = {
    "YYYY": "YEAR", "YY": "YEAR", "YEAR": "YEAR", "MM": "MONTH", "MON": "MONTH", "MONTH": "MONTH",
    "DD": "DAY", "D": "DAY", "DAY": "DAY",
}
_TIME_PARTS = {"HH": "HH24", "HH12": "HH12", "HH24": "HH24", "MI": "MI", "SS": "SS"}
_PLAIN = {  # same name, same arity semantics
    "UPPER", "LOWER", "LENGTH", "ABS", "CEIL", "FLOOR", "MOD", "POWER", "SQRT", "LN", "EXP",
    "INITCAP", "LAST_DAY", "SIGN", "ASCII", "CHR", "SIN", "COS", "TAN", "ASIN", "ACOS", "ATAN",
    "SINH", "COSH", "TANH", "MEDIAN", "STDDEV", "VARIANCE", "REVERSE",
}


def _lit(node) -> Optional[str]:
    return node[1] if node[0] == "str" else None


def _fmt(fmt: str) -> str:
    for a, b in _FMT_SUBS:
        fmt = re.sub(rf"(?<![A-Z]){a}(?![A-Z])", b, fmt)
    return fmt


class _Emitter:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.review: list[str] = []
        self.warnings: list[str] = []
        self.lookups: set[str] = set()

    # ---- coercion helpers -------------------------------------------------
    def val(self, node) -> str:
        sql, kind = self.emit(node)
        return f"CASE WHEN {sql} THEN 1 ELSE 0 END" if kind == BOOL else sql

    def cond(self, node) -> str:
        sql, kind = self.emit(node)
        if kind == BOOL:
            return sql
        if node[0] == "bool":
            return "1 = 1" if node[1] == "TRUE" else "1 = 0"
        return f"({sql}) <> 0"

    def flag(self, msg: str, node_sql: str) -> str:
        self.review.append(msg)
        return f"{node_sql} /* NEEDS_REVIEW: {msg} */"

    # ---- main dispatch ----------------------------------------------------
    def emit(self, n) -> tuple[str, str]:
        k = n[0]
        if k == "str":
            if n[1] == "":
                return "NULL", VAL  # Oracle: '' is NULL
            return "'" + n[1].replace("'", "''") + "'", VAL
        if k == "num":
            return n[1], VAL
        if k == "null":
            return "NULL", VAL
        if k == "bool":
            return ("1" if n[1] == "TRUE" else "0"), VAL
        if k == "id":
            up = n[1].upper()
            if up == "SYSDATE":
                return "SYSDATE", VAL
            if up == "SYSTIMESTAMP":
                return "SYSTIMESTAMP", VAL
            if up in _DD:
                return _DD[up], VAL
            if up == "SESSSTARTTIME":
                return ("TO_TIMESTAMP('{{ run_started_at.strftime(\"%Y-%m-%d %H:%M:%S\") }}', "
                        "'YYYY-MM-DD HH24:MI:SS')"), VAL
            return self.ctx.ref(n[1]), VAL
        if k == "param":
            d = self.ctx.params.get(n[1])
            name = n[1].lower()
            if d is None:
                self.warnings.append(f"parameter ${n[1]} has no default; add var('{name}') to dbt_project.yml")
                return "{{ var('" + name + "') }}", VAL
            if re.fullmatch(r"-?\d+(\.\d+)?", d.strip()):
                return "{{ var('" + name + "', " + d.strip() + ") }}", VAL
            return "'{{ var('" + name + "', '" + d.replace("'", "") + "') }}'", VAL
        if k == "sess":
            return self.flag(f"session parameter ${n[1]} has no dbt equivalent", "NULL"), VAL
        if k == "lkp":
            return self.lookup_call(n), VAL
        if k == "neg":
            return f"-{self.val(n[1])}", VAL
        if k == "not":
            return f"NOT ({self.cond(n[1])})", BOOL
        if k in ("and", "or"):
            return f"({self.cond(n[1])} {k.upper()} {self.cond(n[2])})", BOOL
        if k == "bin":
            return self.binop(n)
        if k == "func":
            return self.func(n[1], n[2])
        raise ExprSyntaxError(f"unknown node {k}")

    def binop(self, n):
        op, l, r = n[1], n[2], n[3]
        if op in ("=", "<>"):
            # Oracle treats '' as NULL, so comparing to '' becomes a NULL test.
            for a, b in ((l, r), (r, l)):
                if b == ("str", ""):
                    self.warnings.append("comparison with '' rewritten as IS [NOT] NULL (Oracle empty-string rule)")
                    return f"{self.val(a)} IS {'NOT ' if op == '<>' else ''}NULL", BOOL
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return f"{self.val(l)} {op} {self.val(r)}", BOOL
        if op == "||":
            return f"({self.val(l)} || {self.val(r)})", VAL
        if op == "%":
            return f"MOD({self.val(l)}, {self.val(r)})", VAL
        return f"({self.val(l)} {op} {self.val(r)})", VAL

    def lookup_call(self, n):
        name = n[1]
        macro = self.ctx.lookup_macros.get(name) or self.ctx.lookup_macros.get(name.upper())
        args = [self.val(a) for a in n[2]]
        if not macro:
            return self.flag(f"unconnected lookup {name} not found in mapping", "NULL")
        self.lookups.add(name)
        quoted = ", ".join("'" + a.replace("\\", "\\\\").replace("'", "\\'") + "'" for a in args)
        return "{{ " + macro + "(" + quoted + ") }}"

    # ---- functions --------------------------------------------------------
    def func(self, name: str, a: list) -> tuple[str, str]:
        ov = self.ctx.overrides.get(name)
        if ov:
            return ov.format(*[self.val(x) for x in a]), VAL
        h = getattr(self, "f_" + name.lower(), None)
        if h:
            return h(a)
        if name in _PLAIN:
            return f"{name}({', '.join(self.val(x) for x in a)})", VAL
        args = ", ".join(self.val(x) for x in a)
        return self.flag(f"unmapped function {name}", f"{name}({args})"), VAL

    def f_iif(self, a):
        if len(a) not in (2, 3):
            return self.flag("IIF needs 2 or 3 arguments", "NULL"), VAL
        els = f" ELSE {self.val(a[2])}" if len(a) == 3 else ""
        return f"CASE WHEN {self.cond(a[0])} THEN {self.val(a[1])}{els} END", VAL

    def f_isnull(self, a):
        return f"{self.val(a[0])} IS NULL", BOOL

    def f_is_spaces(self, a):
        x = self.val(a[0])
        return f"({x} IS NOT NULL AND TRIM({x}) IS NULL)", BOOL

    def f_is_number(self, a):
        return f"VALIDATE_CONVERSION({self.val(a[0])} AS NUMBER) = 1", BOOL

    def f_is_date(self, a):
        fmt = _lit(a[1]) if len(a) > 1 else None
        if fmt is None:
            return self.flag("IS_DATE needs a literal format", "0 = 1"), BOOL
        return f"VALIDATE_CONVERSION({self.val(a[0])} AS DATE, '{_fmt(fmt)}') = 1", BOOL

    def f_in(self, a):
        if len(a) < 2:
            return self.flag("IN needs at least 2 arguments", "0 = 1"), BOOL
        vals, nodes = a[0], list(a[1:])
        ci = None
        if len(nodes) >= 2 and nodes[-1][0] == "num" and nodes[-1][1] in ("0", "1"):
            ci = nodes.pop()[1] == "0"
            self.warnings.append("IN: last argument treated as the case flag")
        left = self.val(vals)
        items = [self.val(x) for x in nodes]
        if ci:
            left = f"UPPER({left})"
            items = [f"UPPER({i})" for i in items]
        return f"{left} IN ({', '.join(items)})", BOOL

    def f_decode(self, a):
        if len(a) < 3:
            return self.flag("DECODE needs at least 3 arguments", "NULL"), VAL
        head, rest = a[0], a[1:]
        default = rest.pop() if len(rest) % 2 == 1 else None
        searched = head == ("bool", "TRUE")
        parts = "CASE" if searched else f"CASE {self.val(head)}"
        for s, r in zip(rest[0::2], rest[1::2]):
            test = self.cond(s) if searched else self.val(s)
            parts += f" WHEN {test} THEN {self.val(r)}"
        if default is not None:
            parts += f" ELSE {self.val(default)}"
        return parts + " END", VAL

    def f_ltrim(self, a):
        return f"LTRIM({', '.join(self.val(x) for x in a)})", VAL

    def f_rtrim(self, a):
        return f"RTRIM({', '.join(self.val(x) for x in a)})", VAL

    def f_substr(self, a):
        return f"SUBSTR({', '.join(self.val(x) for x in a)})", VAL

    def f_instr(self, a):
        if len(a) > 4:
            self.warnings.append("INSTR: comparison-type argument ignored")
        return f"INSTR({', '.join(self.val(x) for x in a[:4])})", VAL

    def f_concat(self, a):
        return "(" + " || ".join(self.val(x) for x in a) + ")", VAL

    def f_lpad(self, a):
        return f"LPAD({', '.join(self.val(x) for x in a)})", VAL

    def f_rpad(self, a):
        return f"RPAD({', '.join(self.val(x) for x in a)})", VAL

    def f_round(self, a):
        return f"ROUND({', '.join(self.val(x) for x in a)})", VAL

    def f_trunc(self, a):
        return f"TRUNC({', '.join(self.val(x) for x in a)})", VAL

    def f_replacestr(self, a):
        # REPLACESTR(case_flag, input, old1, [old2..], new)
        if len(a) < 4:
            return self.flag("REPLACESTR argument count", "NULL"), VAL
        x, olds, new = self.val(a[1]), a[2:-1], self.val(a[-1])
        out = x
        for o in olds:
            out = f"REPLACE({out}, {self.val(o)}, {new})"
        return out, VAL

    def f_replacechr(self, a):
        if len(a) != 4:
            return self.flag("REPLACECHR argument count", "NULL"), VAL
        return f"REPLACE({self.val(a[1])}, {self.val(a[2])}, {self.val(a[3])})", VAL

    def f_reg_replace(self, a):
        return f"REGEXP_REPLACE({', '.join(self.val(x) for x in a)})", VAL

    def f_reg_extract(self, a):
        args = [self.val(x) for x in a]
        if len(args) == 3 and a[2][0] == "num":
            return f"REGEXP_SUBSTR({args[0]}, {args[1]}, 1, 1, NULL, {args[2]})", VAL
        return f"REGEXP_SUBSTR({args[0]}, {args[1]})", VAL

    def f_reg_match(self, a):
        return f"REGEXP_LIKE({self.val(a[0])}, {self.val(a[1])})", BOOL

    def f_to_char(self, a):
        if len(a) == 2 and _lit(a[1]) is not None:
            return f"TO_CHAR({self.val(a[0])}, '{_fmt(_lit(a[1]))}')", VAL
        return f"TO_CHAR({self.val(a[0])})", VAL

    def f_to_date(self, a):
        x = self.val(a[0])
        if len(a) == 1:
            return self.flag("TO_DATE without a format depends on session format", f"TO_DATE({x})"), VAL
        fmt = _lit(a[1])
        if fmt is None:
            return self.flag("TO_DATE with non-literal format", f"TO_DATE({x}, {self.val(a[1])})"), VAL
        o = _fmt(fmt)
        fn = "TO_TIMESTAMP" if "FF" in o else "TO_DATE"
        return f"{fn}({x}, '{o}')", VAL

    def f_to_integer(self, a):
        return f"ROUND(TO_NUMBER({self.val(a[0])}))", VAL

    def f_to_decimal(self, a):
        if len(a) > 1:
            return f"ROUND(TO_NUMBER({self.val(a[0])}), {self.val(a[1])})", VAL
        return f"TO_NUMBER({self.val(a[0])})", VAL

    def f_to_bigint(self, a):
        return self.f_to_integer(a)

    def f_to_float(self, a):
        return f"CAST({self.val(a[0])} AS BINARY_DOUBLE)", VAL

    def f_add_to_date(self, a):
        part = (_lit(a[1]) or "").upper() if len(a) == 3 else ""
        d, n = self.val(a[0]), self.val(a[2]) if len(a) == 3 else "0"
        m = {"DD": f"{d} + {n}", "D": f"{d} + {n}", "HH": f"{d} + ({n}) / 24", "HH24": f"{d} + ({n}) / 24",
             "MI": f"{d} + ({n}) / 1440", "SS": f"{d} + ({n}) / 86400",
             "MM": f"ADD_MONTHS({d}, {n})", "MON": f"ADD_MONTHS({d}, {n})",
             "YYYY": f"ADD_MONTHS({d}, 12 * ({n}))", "YY": f"ADD_MONTHS({d}, 12 * ({n}))"}
        if part not in m:
            return self.flag(f"ADD_TO_DATE part {part or '?'} unsupported", f"{d}"), VAL
        return f"({m[part]})", VAL

    def f_date_diff(self, a):
        part = (_lit(a[2]) or "").upper() if len(a) == 3 else ""
        d1, d2 = self.val(a[0]), self.val(a[1])
        m = {"DD": f"({d1} - {d2})", "D": f"({d1} - {d2})", "HH": f"(({d1} - {d2}) * 24)",
             "MI": f"(({d1} - {d2}) * 1440)", "SS": f"(({d1} - {d2}) * 86400)",
             "MM": f"MONTHS_BETWEEN({d1}, {d2})", "YYYY": f"(MONTHS_BETWEEN({d1}, {d2}) / 12)"}
        if part not in m:
            return self.flag(f"DATE_DIFF part {part or '?'} unsupported", "NULL"), VAL
        if part in ("DD", "D", "HH", "MI", "SS", "MM", "YYYY"):
            self.warnings.append("DATE_DIFF returns fractional values in Oracle; PowerCenter truncates to whole units")
        return m[part], VAL

    def f_get_date_part(self, a):
        part = (_lit(a[1]) or "").upper() if len(a) == 2 else ""
        d = self.val(a[0])
        if part in _DATE_PARTS:
            return f"EXTRACT({_DATE_PARTS[part]} FROM {d})", VAL
        if part in _TIME_PARTS:
            return f"TO_NUMBER(TO_CHAR({d}, '{_TIME_PARTS[part]}'))", VAL
        return self.flag(f"GET_DATE_PART part {part or '?'} unsupported", "NULL"), VAL

    # aggregates -----------------------------------------------------------
    def _agg(self, fn, a, distinct=False):
        if not a:
            return f"{fn}(*)", VAL
        x = self.val(a[0])
        if len(a) > 1:
            x = f"CASE WHEN {self.cond(a[1])} THEN {x} END"
        return f"{fn}({'DISTINCT ' if distinct else ''}{x})", VAL

    def f_sum(self, a):
        return self._agg("SUM", a)

    def f_avg(self, a):
        return self._agg("AVG", a)

    def f_min(self, a):
        return self._agg("MIN", a)

    def f_max(self, a):
        return self._agg("MAX", a)

    def f_count(self, a):
        if a and a[0] == ("id", "*"):
            return "COUNT(*)", VAL
        return self._agg("COUNT", a)

    def f_first(self, a):
        return self.flag("FIRST depends on input row order", f"MIN({self.val(a[0])})"), VAL

    def f_last(self, a):
        return self.flag("LAST depends on input row order", f"MAX({self.val(a[0])})"), VAL

    # no equivalent --------------------------------------------------------
    def f_error(self, a):
        return self.flag("ERROR() row rejection has no SQL equivalent; add a dbt test or reject model", "NULL"), VAL

    def f_abort(self, a):
        return self.flag("ABORT() has no SQL equivalent", "NULL"), VAL

    def f_setvariable(self, a):
        return self.flag("SETVARIABLE has no SQL equivalent", "NULL"), VAL

    f_setmaxvariable = f_setminvariable = f_setcountvariable = f_setvariable


def translate(text: str, ctx: Optional[Context] = None) -> Translation:
    """Translate one expression. Never raises: syntax errors become review items."""
    ctx = ctx or Context()
    em = _Emitter(ctx)
    try:
        ast = parse(text)
        sql = em.val(ast)
    except ExprSyntaxError as e:
        return Translation(
            sql=f"NULL /* NEEDS_REVIEW: cannot parse expression: {e} */",
            review=[f"cannot parse expression ({e}): {text}"],
        )
    return Translation(sql=sql, review=em.review, warnings=em.warnings, lookups=em.lookups)


def translate_condition(text: str, ctx: Optional[Context] = None) -> Translation:
    """Translate an expression used as a filter / router condition."""
    ctx = ctx or Context()
    em = _Emitter(ctx)
    try:
        sql = em.cond(parse(text))
    except ExprSyntaxError as e:
        return Translation(
            sql=f"1 = 0 /* NEEDS_REVIEW: cannot parse condition: {e} */",
            review=[f"cannot parse condition ({e}): {text}"],
        )
    return Translation(sql=sql, review=em.review, warnings=em.warnings, lookups=em.lookups)
