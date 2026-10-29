"""Parse a PowerCenter XML export into the IR.

Every XML node that the parser sees but does not understand is recorded in
``Mapping.unparsed_nodes`` so it can reach the manual-review report instead of
being dropped silently.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from .ir import (Connector, Instance, Mapping, Port, Table, Transformation)

KNOWN_MAPPING_CHILDREN = {
    "TRANSFORMATION", "INSTANCE", "CONNECTOR", "TARGETLOADORDER",
    "MAPPINGVARIABLE", "ERPINFO", "FIELDDEPENDENCY",
}


def _int(v: str | None) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def _port(el: etree._Element) -> Port:
    a = el.attrib
    return Port(
        name=a.get("NAME", ""),
        datatype=a.get("DATATYPE", "string"),
        precision=_int(a.get("PRECISION")),
        scale=_int(a.get("SCALE")),
        porttype=a.get("PORTTYPE", "INPUT/OUTPUT"),
        expression=a.get("EXPRESSION") or None,
        group=a.get("GROUP") or None,
        key="PRIMARY" in a.get("KEYTYPE", "").upper(),
        default=a.get("DEFAULTVALUE") or None,
        expr_type=a.get("EXPRESSIONTYPE", "GENERAL"),
        sort_dir=a.get("SORTDIRECTION") or None,
        sort_key=a.get("ISSORTKEY", "NO").upper() == "YES",
        ref_field=a.get("REF_FIELD") or None,
        not_null=a.get("NULLABLE", "NULL").upper() == "NOTNULL",
    )


def _table(el: etree._Element, kind: str) -> Table:
    fields = []
    for f in el.findall(f"{kind}FIELD"):
        p = _port(f)
        p.porttype = "INPUT/OUTPUT"
        fields.append(p)
    return Table(
        name=el.get("NAME", ""),
        kind=kind,
        dbdname=el.get("DBDNAME", ""),
        owner=el.get("OWNERNAME", ""),
        fields=fields,
    )


def _transformation(el: etree._Element) -> Transformation:
    ports = [_port(f) for f in el.findall("TRANSFORMFIELD")]
    attrs = {t.get("NAME", ""): t.get("VALUE", "") for t in el.findall("TABLEATTRIBUTE")}
    groups = [
        (g.get("NAME", ""), g.get("EXPRESSION", ""))
        for g in el.findall("GROUP") if g.get("TYPE", "").upper() != "INPUT"
    ]
    return Transformation(
        name=el.get("NAME", ""),
        type=el.get("TYPE", ""),
        reusable=el.get("REUSABLE", "NO").upper() == "YES",
        ports=ports,
        attrs=attrs,
        groups=groups,
    )


def parse_xml(path: str | Path) -> list[Mapping]:
    """Return one Mapping per MAPPING node in the file."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()
    mappings: list[Mapping] = []

    for folder in root.iter("FOLDER"):
        sources = {s.get("NAME", ""): _table(s, "SOURCE") for s in folder.findall("SOURCE")}
        targets = {t.get("NAME", ""): _table(t, "TARGET") for t in folder.findall("TARGET")}
        reusable = {
            t.get("NAME", ""): _transformation(t) for t in folder.findall("TRANSFORMATION")
        }
        for m in folder.findall("MAPPING"):
            mp = Mapping(
                name=m.get("NAME", ""), folder=folder.get("NAME", ""),
                sources=dict(sources), targets=dict(targets),
            )
            for t in m.findall("TRANSFORMATION"):
                tr = _transformation(t)
                mp.transformations[tr.name] = tr
            for i in m.findall("INSTANCE"):
                inst = Instance(
                    name=i.get("NAME", ""),
                    type=i.get("TYPE", ""),
                    transformation_name=i.get("TRANSFORMATION_NAME", i.get("NAME", "")),
                    transformation_type=i.get("TRANSFORMATION_TYPE", ""),
                )
                mp.instances[inst.name] = inst
                # attach only the reusable definitions this mapping actually instantiates
                if inst.type == "TRANSFORMATION" and inst.transformation_name in reusable:
                    mp.transformations.setdefault(inst.transformation_name, reusable[inst.transformation_name])
            for c in m.findall("CONNECTOR"):
                mp.connectors.append(Connector(
                    from_instance=c.get("FROMINSTANCE", ""),
                    from_field=c.get("FROMFIELD", ""),
                    to_instance=c.get("TOINSTANCE", ""),
                    to_field=c.get("TOFIELD", ""),
                ))
            for v in m.findall("MAPPINGVARIABLE"):
                mp.parameters[v.get("NAME", "").lstrip("$")] = v.get("DEFAULTVALUE", "")
            for child in m:
                if child.tag not in KNOWN_MAPPING_CHILDREN and isinstance(child.tag, str):
                    mp.unparsed_nodes.append(f"{child.tag}:{child.get('NAME', '')}")
            mappings.append(mp)
    return mappings
