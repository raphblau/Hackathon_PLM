"""Lecture d'un export XPDMXML (3DEXPERIENCE) et mise à plat de la nomenclature.

Le fichier contient trois familles d'objets :
- ``Product``      : les articles (référence, révision, maturité, masse, matière…)
- ``ProductInst``  : les liens parent → enfant (``Owned`` → ``Instancing``)
- ``Rep3DAggr``    : les représentations 3D rattachées à un article

Chaque ligne produite garde le numéro de ligne XML d'origine (``xml_line``,
``product_line``) : c'est ce qui permet de citer la source de chaque chiffre.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from lxml import etree

NS = "{http://www.3ds.com/xsd/XPDMXML}"

ROLE_LABEL = {
    "assembly": "Assemblage",
    "part": "Pièce (article EI)",
    "geometry": "Représentation 3D",
}


@dataclass
class PlmModel:
    """Le paquet XML, mis à plat et prêt pour l'analyse."""

    products: pd.DataFrame          # un objet Product par ligne (xml_id en index)
    bom: pd.DataFrame               # une occurrence par ligne (occ unique)
    info: pd.DataFrame              # InfoHeader (name, value)
    source_name: str
    children: dict[str, list[str]] = field(default_factory=dict)  # occ → occs enfants

    # ---- accès pratiques -------------------------------------------------
    def row(self, occ: str) -> pd.Series:
        return self.bom.loc[self.bom["occ"] == occ].iloc[0]

    def product(self, xml_id: str) -> pd.Series:
        return self.products.loc[xml_id]

    def ancestors(self, occ: str) -> list[pd.Series]:
        """Les assemblages parents d'une occurrence, du plus proche à la racine."""
        out, by_occ = [], self.bom.set_index("occ", drop=False)
        parent = by_occ.loc[occ, "parent_occ"]
        while parent:
            out.append(by_occ.loc[parent])
            parent = by_occ.loc[parent, "parent_occ"]
        return out

    def where_used(self, xml_id: str) -> pd.DataFrame:
        """Toutes les occurrences d'un article."""
        return self.bom.loc[self.bom["xml_id"] == xml_id]

    def parts(self) -> pd.DataFrame:
        """Une ligne par référence de pièce avec sa quantité."""
        p = self.bom.loc[self.bom["role"] == "part"]
        agg = (
            p.groupby("xml_id")
            .agg(
                short=("short", "first"), name=("name", "first"),
                part_number=("part_number", "first"), rev=("rev", "first"),
                rev_index=("rev_index", "first"), maturity=("maturity", "first"),
                material=("material", "first"), unit_mass=("mass_kg", "first"),
                subassembly=("subassembly", "first"), product_line=("product_line", "first"),
                description=("description", "first"), qty=("occ", "count"),
            )
            .reset_index()
        )
        agg["total_mass"] = (agg["unit_mass"] * agg["qty"]).round(3)
        return agg


def _text(el, tag: str) -> str:
    x = el.find(NS + tag)
    return (x.text or "").strip() if x is not None else ""


def short_name(name: str) -> str:
    for prefix in ("ECAR-EI_", "ECAR-3D_", "ECAR-3D ", "ECAR-"):
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name


def load(path: str | Path) -> PlmModel:
    path = Path(path)
    root = etree.parse(str(path)).getroot()

    info = pd.DataFrame(
        [{"name": i.get("name"), "value": i.get("value")} for i in root.find(NS + "InfoHeader")]
    )

    products: dict[str, dict] = {}
    instances: list[dict] = []
    reps: dict[str, list[str]] = defaultdict(list)

    for el in root.find(NS + "ProductStructure"):
        tag = etree.QName(el).localname
        if tag == "Product":
            products[el.get("id")] = {
                "xml_id": el.get("id"),
                "product_line": el.sourceline,
                "part_number": _text(el, "ID"),
                "name": _text(el, "Name"),
                "short": short_name(_text(el, "Name")),
                "description": _text(el, "Description"),
                "rev": _text(el, "RevisionName"),
                "rev_index": int(_text(el, "RevisionIndex") or 0),
                "maturity": _text(el, "Maturity"),
                "lifecycle": _text(el, "Lifecycle"),
                "created": _text(el, "TimeCreated"),
                "modified": _text(el, "TimeModified"),
                "owner": _text(el, "Owner"),
                "organization": _text(el, "Organization"),
                "project": _text(el, "Project"),
                "mass_kg": float(_text(el, "WCGEquivalentComputedExt.V_WCG_Mass") or 0),
                "material": _text(el, "Material"),
            }
        elif tag == "ProductInst":
            instances.append({
                "inst": el.get("id"),
                "xml_line": el.sourceline,
                "instance_name": _text(el, "ID"),
                "parent": _text(el, "Owned"),
                "child": _text(el, "Instancing"),
                "order": float(_text(el, "ChildOrder") or 0),
            })
        elif tag == "Rep3DAggr":
            reps[_text(el, "Owned")].append(_text(el, "Name"))

    kids: dict[str, list[dict]] = defaultdict(list)
    for i in instances:
        kids[i["parent"]].append(i)
    for k in kids:
        kids[k].sort(key=lambda x: x["order"])

    roots = [pid for pid in products if pid not in {i["child"] for i in instances}]
    rows: list[dict] = []
    by_occ: dict[str, dict] = {}
    children: dict[str, list[str]] = defaultdict(list)

    def role_of(pid: str, parent_row: dict | None) -> str:
        ch = kids[pid]
        if not ch:
            # géométrie 3D seule sous un article → représentation ; sinon pièce
            if parent_row and parent_row["role"] == "part":
                return "geometry"
            return "part"
        if len(ch) == 1 and not kids[ch[0]["child"]]:
            return "part"
        return "assembly"

    def walk(pid: str, inst: dict | None, parent_row: dict | None, level: int, path: list[str]):
        p = products[pid]
        role = role_of(pid, parent_row)
        occ = (parent_row["occ"] + "." if parent_row and parent_row["occ"] != "0" else "") + (
            inst["inst"].replace("ID_", "") if inst else "0"
        )
        sub = parent_row["subassembly"] if parent_row and level > 1 else (p["short"] if level == 1 else "")
        row = {
            **p,
            "occ": occ,
            "inst": inst["inst"] if inst else "ROOT",
            "xml_line": inst["xml_line"] if inst else p["product_line"],
            "instance_name": inst["instance_name"] if inst else "(racine)",
            "parent_occ": parent_row["occ"] if parent_row else "",
            "parent_short": parent_row["short"] if parent_row else "",
            "level": level,
            "role": role,
            "role_label": ROLE_LABEL[role],
            "children": len(kids[pid]),
            "subassembly": sub,
            "path": " › ".join(path),
            "rep3d": ", ".join(reps.get(pid, [])),
        }
        rows.append(row)
        by_occ[occ] = row
        if parent_row:
            children[parent_row["occ"]].append(occ)
        for c in kids[pid]:
            walk(c["child"], c, row, level + 1, path + [products[c["child"]]["short"]])

    for r in roots:
        walk(r, None, None, 0, [products[r]["short"]])

    bom = pd.DataFrame(rows)
    prod_df = pd.DataFrame(list(products.values())).set_index("xml_id", drop=False)
    return PlmModel(products=prod_df, bom=bom, info=info, source_name=path.name, children=dict(children))
