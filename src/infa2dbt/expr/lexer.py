"""Tokenizer and recursive-descent parser for the PowerCenter expression language.

AST nodes are plain tuples:
  ('str', text) ('num', text) ('null',) ('bool', 'TRUE'|'FALSE')
  ('id', name) ('param', name) ('sess', name)
  ('func', NAME, [args]) ('lkp', name, [args])
  ('bin', op, l, r) ('neg', x) ('not', x) ('and', l, r) ('or', l, r)
"""
from __future__ import annotations

import re

TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<str>'(?:[^']|'')*')
  | (?P<num>\d+\.\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|\d+(?:[eE][+-]?\d+)?)
  | (?P<lkp>:LKP\.[A-Za-z_][\w$#]*)
  | (?P<param>\$\$[A-Za-z_]\w*)
  | (?P<sess>\$[A-Za-z_]\w*)
  | (?P<qid>"(?:[^"]|"")*")
  | (?P<id>[A-Za-z_][\w$#.]*)
  | (?P<op>\|\||<>|!=|<=|>=|=|<|>|\+|-|\*|/|%|\(|\)|,)
    """,
    re.VERBOSE | re.IGNORECASE,
)


class ExprSyntaxError(ValueError):
    pass


def tokenize(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise ExprSyntaxError(f"unexpected character {text[pos]!r} at {pos}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append((kind, m.group()))
    return out


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self, off=0):
        j = self.i + off
        return self.t[j] if j < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def accept_op(self, *ops):
        k, v = self.peek()
        if k == "op" and v in ops:
            self.i += 1
            return v
        return None

    def accept_kw(self, kw):
        k, v = self.peek()
        if k == "id" and v.upper() == kw:
            self.i += 1
            return True
        return False

    # precedence: OR < AND < NOT < comparison < || < + - < * / % < unary
    def expr(self):
        node = self.and_()
        while self.accept_kw("OR"):
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.accept_kw("AND"):
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.accept_kw("NOT"):
            return ("not", self.not_())
        return self.cmp_()

    def cmp_(self):
        node = self.concat()
        op = self.accept_op("=", "<>", "!=", "<=", ">=", "<", ">")
        while op:
            node = ("bin", "<>" if op == "!=" else op, node, self.concat())
            op = self.accept_op("=", "<>", "!=", "<=", ">=", "<", ">")
        return node

    def concat(self):
        node = self.add()
        while self.accept_op("||"):
            node = ("bin", "||", node, self.add())
        return node

    def add(self):
        node = self.mul()
        op = self.accept_op("+", "-")
        while op:
            node = ("bin", op, node, self.mul())
            op = self.accept_op("+", "-")
        return node

    def mul(self):
        node = self.unary()
        op = self.accept_op("*", "/", "%")
        while op:
            node = ("bin", op, node, self.unary())
            op = self.accept_op("*", "/", "%")
        return node

    def unary(self):
        if self.accept_op("-"):
            return ("neg", self.unary())
        if self.accept_op("+"):
            return self.unary()
        return self.primary()

    def args(self):
        out = []
        if self.accept_op(")"):
            return out
        while True:
            out.append(self.expr())
            if self.accept_op(")"):
                return out
            if not self.accept_op(","):
                raise ExprSyntaxError("expected ',' or ')' in argument list")

    def primary(self):
        k, v = self.take()
        if k is None:
            raise ExprSyntaxError("unexpected end of expression")
        if k == "str":
            return ("str", v[1:-1].replace("''", "'"))
        if k == "num":
            return ("num", v)
        if k == "param":
            return ("param", v[2:])
        if k == "sess":
            return ("sess", v[1:])
        if k == "lkp":
            if not self.accept_op("("):
                raise ExprSyntaxError("lookup call needs '('")
            return ("lkp", v[5:], self.args())
        if k == "qid":
            return ("id", v[1:-1].replace('""', '"'))
        if k == "op" and v == "(":
            node = self.expr()
            if not self.accept_op(")"):
                raise ExprSyntaxError("missing ')'")
            return node
        if k == "id":
            up = v.upper()
            if up == "NULL":
                return ("null",)
            if up in ("TRUE", "FALSE"):
                return ("bool", up)
            if self.peek() == ("op", "("):
                self.take()
                return ("func", up, self.args())
            return ("id", v)
        raise ExprSyntaxError(f"unexpected token {v!r}")


def parse(text: str):
    p = _Parser(tokenize(text))
    node = p.expr()
    if p.i != len(p.t):
        raise ExprSyntaxError(f"trailing input near {p.t[p.i][1]!r}")
    return node
