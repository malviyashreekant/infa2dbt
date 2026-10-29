"""Oracle datatype and identifier helpers."""
from __future__ import annotations

import re

_MAP = {
    "string": "VARCHAR2", "nstring": "NVARCHAR2", "char": "CHAR", "varchar2": "VARCHAR2",
    "varchar": "VARCHAR2", "nvarchar2": "NVARCHAR2", "text": "VARCHAR2",
    "date/time": "TIMESTAMP", "date": "DATE", "timestamp": "TIMESTAMP",
    "integer": "NUMBER(10,0)", "small integer": "NUMBER(5,0)", "bigint": "NUMBER(19,0)",
    "number": "NUMBER", "decimal": "NUMBER", "double": "BINARY_DOUBLE", "real": "BINARY_FLOAT",
}


def oracle_type(datatype: str, precision: int = 0, scale: int = 0) -> str:
    datatype = re.sub(r"\(.*\)", "", datatype)  # "number(p,s)" -> "number"
    base = _MAP.get(datatype.lower().strip(), "VARCHAR2")
    if datatype.lower().strip() == "date/time":
        return "TIMESTAMP" if precision > 19 else "DATE"  # PowerCenter uses precision 19 for Oracle DATE
    if base in ("VARCHAR2", "NVARCHAR2", "CHAR"):
        return f"{base}({precision or 4000})" if base != "CHAR" else f"CHAR({precision or 1})"
    if base == "NUMBER" and precision:
        return f"NUMBER({precision},{scale})"
    return base


def is_numeric(datatype: str) -> bool:
    return oracle_type(datatype).startswith(("NUMBER", "BINARY"))


def ident(name: str) -> str:
    """Safe lowercase snake identifier for dbt models and CTEs."""
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    if not s or s[0].isdigit():
        s = "x_" + s
    return s


RESERVED = {
    "ACCESS", "ADD", "ALL", "ALTER", "AND", "ANY", "AS", "ASC", "AUDIT", "BETWEEN", "BY", "CHAR",
    "CHECK", "CLUSTER", "COLUMN", "COMMENT", "COMPRESS", "CONNECT", "CREATE", "CURRENT", "DATE",
    "DECIMAL", "DEFAULT", "DELETE", "DESC", "DISTINCT", "DROP", "ELSE", "EXCLUSIVE", "EXISTS",
    "FILE", "FLOAT", "FOR", "FROM", "GRANT", "GROUP", "HAVING", "IDENTIFIED", "IMMEDIATE", "IN",
    "INCREMENT", "INDEX", "INITIAL", "INSERT", "INTEGER", "INTERSECT", "INTO", "IS", "LEVEL",
    "LIKE", "LOCK", "LONG", "MAXEXTENTS", "MINUS", "MODE", "MODIFY", "NOAUDIT", "NOCOMPRESS",
    "NOT", "NOWAIT", "NULL", "NUMBER", "OF", "OFFLINE", "ON", "ONLINE", "OPTION", "OR", "ORDER",
    "PCTFREE", "PRIOR", "PUBLIC", "RAW", "RENAME", "RESOURCE", "REVOKE", "ROW", "ROWID", "ROWNUM",
    "ROWS", "SELECT", "SESSION", "SET", "SHARE", "SIZE", "SMALLINT", "START", "SUCCESSFUL",
    "SYNONYM", "SYSDATE", "TABLE", "THEN", "TO", "TRIGGER", "UID", "UNION", "UNIQUE", "UPDATE",
    "USER", "VALIDATE", "VALUES", "VARCHAR", "VARCHAR2", "VIEW", "WHENEVER", "WHERE", "WITH",
}


def col(name: str) -> str:
    """Oracle column identifier: upper-case, double-quoted when it is a reserved word."""
    s = re.sub(r"[^0-9a-zA-Z_$#]+", "_", name).strip("_").upper()
    if not s or not s[0].isalpha():
        s = "C_" + s
    return f'"{s}"' if s in RESERVED else s


def cte_name(name: str) -> str:
    s = ident(name)
    return f"t_{s}" if s.upper() in RESERVED or s in ("src", "s", "m", "d", "lkp") else s
