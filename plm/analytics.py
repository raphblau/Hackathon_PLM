"""Analyses métier : criticité, matières, fournisseurs, impact d'un changement, qualité des données.

Les coûts, risques et fournisseurs ne sont PAS dans l'export OEM : ils viennent du
référentiel ``data/materials_ref.csv`` (hypothèses d'exemple, à remplacer par les
données achats). Chaque fonction renvoie des DataFrames prêts à afficher.
"""
from __future__ import annotations

import pandas as pd

from .parser import PlmModel

RISK_LABEL = {1: "Faible", 2: "Moyen", 3: "Élevé"}
CATALOG_CATEGORIES = {"Roulementier", "Visserie", "Joints élastomères"}

# Pondérations du score de criticité (sur 100)
W_RISK, W_COST, W_MASS, W_QTY = 0.4, 0.3, 0.2, 0.1
REV_PENALTY, REV_PENALTY_CAP = 5, 15


def load_materials(path) -> pd.DataFrame:
    m = pd.read_csv(path)
    m["kind"] = m["supplier_category"].map(
        lambda c: "Achat catalogue" if c in CATALOG_CATEGORIES else "Fabriqué sur plan"
    )
    m["risk_label"] = m["supply_risk"].map(RISK_LABEL)
    return m


def next_rev(rev: str) -> str:
    return chr(ord(rev) + 1) if rev else ""


# ---------------------------------------------------------------------------
def part_risk(model: PlmModel, materials: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par référence de pièce : coût estimé, score de criticité, niveau."""
    p = model.parts().merge(materials, on="material", how="left")
    p["price_eur_kg"] = p["price_eur_kg"].fillna(0)
    p["process_factor"] = p["process_factor"].fillna(1)
    p["supply_risk"] = p["supply_risk"].fillna(1).astype(int)
    p["risk_label"] = p["supply_risk"].map(RISK_LABEL)
    p["supplier_category"] = p["supplier_category"].fillna("Non renseigné")
    p["kind"] = p["kind"].fillna("Fabriqué sur plan")
    p["family"] = p["family"].fillna("Inconnue")
    p["est_cost"] = (p["total_mass"] * p["price_eur_kg"] * p["process_factor"]).round(1)
    p["co2_kg"] = (p["total_mass"] * p["co2_kg_per_kg"].fillna(0)).round(2)

    rev_pen = ((p["rev_index"] - 1).clip(lower=0) * REV_PENALTY).clip(upper=REV_PENALTY_CAP)
    score = 100 * (
        W_RISK * (p["supply_risk"] - 1) / 2
        + W_COST * p["est_cost"] / p["est_cost"].max()
        + W_MASS * p["total_mass"] / p["total_mass"].max()
        + W_QTY * p["qty"] / p["qty"].max()
    )
    p["score"] = (score + rev_pen).round().clip(upper=100).astype(int)
    p["tier"] = pd.cut(p["score"], [-1, 34, 59, 100], labels=["Faible", "À surveiller", "Critique"]).astype(str)
    p["unstable"] = p["rev_index"] >= 3
    return p.sort_values("score", ascending=False).reset_index(drop=True)


def by_material(risk: pd.DataFrame) -> pd.DataFrame:
    g = risk.groupby(["material", "family", "supply_risk", "risk_label"], as_index=False).agg(
        mass=("total_mass", "sum"), cost=("est_cost", "sum"), co2=("co2_kg", "sum"), refs=("xml_id", "count")
    )
    g["mass"] = g["mass"].round(3)
    return g.sort_values("mass", ascending=False)


def by_supplier(risk: pd.DataFrame) -> pd.DataFrame:
    g = risk.groupby(["supplier_category", "kind"], as_index=False).agg(
        refs=("xml_id", "count"), qty=("qty", "sum"), mass=("total_mass", "sum"),
        cost=("est_cost", "sum"), risk=("supply_risk", "max"),
    )
    g["cost_share"] = g["cost"] / g["cost"].sum()
    g["risk_label"] = g["risk"].map(RISK_LABEL)
    return g.sort_values("cost", ascending=False)


def price_shock(risk: pd.DataFrame, family: str, pct: float) -> pd.DataFrame:
    """Surcoût si le prix d'une famille de matières varie de ``pct`` %.

    Le choc ne s'applique qu'à la part matière du coût (coût / facteur procédé).
    """
    r = risk.copy()
    material_share = r["est_cost"] / r["process_factor"].replace(0, 1)
    r["extra_cost"] = (material_share * pct / 100).where(r["family"] == family, 0).round(1)
    return r.loc[r["extra_cost"] != 0].sort_values("extra_cost", ascending=False)


# ---------------------------------------------------------------------------
def change_impact(model: PlmModel, risk: pd.DataFrame, materials: pd.DataFrame,
                  xml_id: str, new_material: str) -> dict:
    """Impact du remplacement de la matière d'une pièce, à volume constant."""
    part = risk.loc[risk["xml_id"] == xml_id].iloc[0]
    a = materials.loc[materials["material"] == part["material"]]
    b = materials.loc[materials["material"] == new_material].iloc[0]
    if a.empty:
        raise ValueError(f"Matière actuelle inconnue du référentiel : {part['material']}")
    a = a.iloc[0]

    old_mass = part["unit_mass"] * part["qty"]
    new_unit = part["unit_mass"] * b["density_g_cm3"] / a["density_g_cm3"]
    new_mass = new_unit * part["qty"]
    old_cost = old_mass * a["price_eur_kg"] * a["process_factor"]
    new_cost = new_mass * b["price_eur_kg"] * b["process_factor"]

    occs = model.where_used(xml_id)
    impacted, seen = [], set()
    for _, o in occs.iterrows():
        if o["xml_id"] not in seen:
            seen.add(o["xml_id"])
            impacted.append(o)
        for anc in model.ancestors(o["occ"]):
            if anc["occ"] not in seen:
                seen.add(anc["occ"])
                impacted.append(anc)
    imp = pd.DataFrame(impacted).sort_values("level", ascending=False)
    imp["next_rev"] = imp["rev"].map(next_rev)

    return {
        "part": part, "from": a, "to": b,
        "old_unit_mass": part["unit_mass"], "new_unit_mass": round(new_unit, 3), "qty": int(part["qty"]),
        "d_mass": round(new_mass - old_mass, 3), "d_mass_pct": (new_mass - old_mass) / old_mass,
        "d_cost": round(new_cost - old_cost, 1), "d_cost_pct": (new_cost - old_cost) / old_cost if old_cost else 0,
        "d_co2": round(new_mass * b["co2_kg_per_kg"] - old_mass * a["co2_kg_per_kg"], 2),
        "supplier_change": a["supplier_category"] != b["supplier_category"],
        "family_change": a["family"] != b["family"],
        "impacted": imp[["occ", "short", "role_label", "rev", "next_rev", "maturity", "part_number", "xml_line", "product_line", "level"]],
    }


# ---------------------------------------------------------------------------
def quality_checks(model: PlmModel) -> list[dict]:
    bom, prod = model.bom, model.products
    parts = bom.loc[bom["role"] == "part"]
    part_prod = parts.drop_duplicates("xml_id")

    multi = part_prod.groupby("name")["rev"].nunique()
    multi_names = multi[multi > 1].index.tolist()

    helper = bom.loc[bom["description"].str.contains("single", case=False, na=False)]
    helper_parts = set()
    for _, h in helper.iterrows():
        helper_parts.add(h["parent_occ"] if h["role"] == "geometry" else h["occ"])
    for _, r in parts.iterrows():
        sib = parts.loc[parts["parent_occ"] == r["parent_occ"], "short"]
        if (sib == r["short"] + "s").any():
            helper_parts.add(r["occ"])
    helper_mass = bom.loc[bom["occ"].isin(helper_parts), "mass_kg"].sum()

    asm = bom.loc[bom["role"] == "assembly"]
    ok_roll = sum(
        abs(bom.loc[bom["parent_occ"] == a["occ"], "mass_kg"].sum() - a["mass_kg"]) < 0.01
        for _, a in asm.iterrows()
    )
    return [
        dict(level="Alerte", title="Aucune référence libérée", count=int((prod["maturity"] != "RELEASED").sum()), total=len(prod),
             text="Tous les objets sont IN_WORK : la définition peut encore changer. Toute décision d'industrialisation reste provisoire."),
        dict(level="Alerte", title="Même nom, plusieurs révisions", count=int(part_prod["name"].isin(multi_names).sum()),
             text=f"Objets différents sous un même nom avec des révisions différentes : {', '.join(multi_names) or '—'}."),
        dict(level="À vérifier", title="Géométrie d'aide comptée dans la masse", count=len(helper_parts),
             text=f"Dent de stator et bobine unitaires (« single ») comptées en plus du stator complet : {helper_mass:.2f} kg possiblement comptés deux fois."),
        dict(level="À vérifier", title="Géométrie 3D instanciée sans article", count=int(parts["name"].str.startswith("ECAR-3D").sum()),
             text="Représentations 3D placées directement dans le carter, sans article d'ingénierie parent."),
        dict(level="À vérifier", title="Noms d'instance hors convention", count=int(bom["instance_name"].str.contains("^ECAR-|Hackathon", regex=True).sum()),
             text="Instances nommées avec le préfixe ECAR- ou un reste de copie (« Hackathon »)."),
        dict(level="À vérifier", title="Organisation non renseignée", count=int((prod["organization"] == "Company Name").sum()),
             text="Le champ Organisation contient la valeur par défaut « Company Name »."),
        dict(level="Info", title="Descriptions vides", count=int((prod["description"] == "").sum()), total=len(prod),
             text="La plupart des objets n'ont pas de description, ce qui limite la recherche en langage naturel."),
        dict(level="OK", title="Masses cohérentes", count=int(ok_roll), total=len(asm),
             text="Assemblages dont la masse égale la somme de leurs enfants."),
        dict(level="OK", title="Matière renseignée sur les pièces", count=int((parts["material"] == "").sum()),
             text="Pièces sans matière (les assemblages n'en ont pas, c'est normal)."),
    ]


def kpis(model: PlmModel, risk: pd.DataFrame) -> dict:
    root = model.bom.loc[model.bom["level"] == 0].iloc[0]
    bm = by_material(risk)
    return {
        "total_mass": root["mass_kg"],
        "products": len(model.products),
        "part_refs": len(risk),
        "part_occ": int(risk["qty"].sum()),
        "assemblies": int((model.bom["role"] == "assembly").sum()),
        "released_pct": (model.products["maturity"] == "RELEASED").mean(),
        "materials": len(bm),
        "high_risk_mass_pct": bm.loc[bm["supply_risk"] == 3, "mass"].sum() / bm["mass"].sum(),
        "est_cost": risk["est_cost"].sum(),
        "critical": int((risk["tier"] == "Critique").sum()),
        "watch": int((risk["tier"] == "À surveiller").sum()),
        "root_line": int(root["product_line"]),
    }
