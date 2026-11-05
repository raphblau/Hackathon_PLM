"""Catalogue fournisseurs, contrats, registre de risques, couverture, prévision de prix,
et mise à jour de la nomenclature (ajouts manuels).

Tout ce qui est ici complète l'export OEM avec des données du fournisseur (catalogue,
contrats) ou des hypothèses d'exemple (prévisions). Rien ne vient du XML.
"""
from __future__ import annotations

import copy
from datetime import date

import numpy as np
import pandas as pd

from .parser import PlmModel, ROLE_LABEL

TODAY = date(2026, 11, 4)

# Volatilité annuelle et dérive (hypothèses d'exemple) par famille de matière, pour la simulation de prix
FAMILY_MARKET = {
    "Aluminium": dict(vol=0.18, drift=0.04, index="LME Aluminium", hedge=True),
    "Cuivre": dict(vol=0.24, drift=0.06, index="LME Copper", hedge=True),
    "Acier": dict(vol=0.10, drift=0.02, index="Indice acier UE (HRC)", hedge=False),
    "Acier électrique": dict(vol=0.14, drift=0.05, index="Tôle NO (indice fournisseur)", hedge=False),
    "Fonte": dict(vol=0.08, drift=0.02, index="Ferraille + énergie", hedge=False),
    "Élastomère": dict(vol=0.12, drift=0.07, index="Fluoropolymères", hedge=False),
    "Polymère": dict(vol=0.15, drift=0.03, index="PA66 contrat", hedge=False),
    "Magnésium": dict(vol=0.22, drift=0.05, index="Magnésium Chine FOB", hedge=False),
}


# ---------------------------------------------------------------------------
# Fournisseurs et contrats
# ---------------------------------------------------------------------------
def load_suppliers(path) -> pd.DataFrame:
    s = pd.read_csv(path, parse_dates=["contract_start", "contract_end"])
    s["primary"] = s["primary"].astype(str).str.lower().eq("true")
    s["months_left"] = ((s["contract_end"] - pd.Timestamp(TODAY)).dt.days / 30.44).round(1)
    s["contract_status"] = s.apply(_contract_status, axis=1)
    return s


def _contract_status(r) -> str:
    if pd.isna(r["contract_end"]):
        return "Sans contrat"
    if r["months_left"] < 0:
        return "Expiré"
    if r["months_left"] <= 3:
        return "Expire < 3 mois"
    if r["months_left"] <= 6:
        return "Expire < 6 mois"
    return "En cours"


def primary_supplier_map(suppliers: pd.DataFrame) -> pd.DataFrame:
    """Un fournisseur principal par catégorie (nom, note, mois restants)."""
    p = suppliers.loc[suppliers["primary"]].drop_duplicates("supplier_category")
    return p.set_index("supplier_category")[["supplier_id", "name", "country", "rating", "esg_index", "answer_time_days", "months_left", "contract_status", "contract_type"]]


def contract_timeline(suppliers: pd.DataFrame) -> pd.DataFrame:
    t = suppliers.dropna(subset=["contract_end"]).copy()
    t["start"] = t["contract_start"].fillna(pd.Timestamp(TODAY))
    return t


# ---------------------------------------------------------------------------
# Simulation de prix matière (hypothèse d'exemple, déterministe)
# ---------------------------------------------------------------------------
def price_series(materials: pd.DataFrame, months_back: int = 12, months_fwd: int = 12, shock: dict | None = None) -> pd.DataFrame:
    """Indice de prix mensuel par famille (base 100 = aujourd'hui), passé simulé et prévision.

    ``shock`` : {famille: pct} applique un choc instantané à la prévision.
    """
    rows = []
    for i, (fam, mk) in enumerate(FAMILY_MARKET.items()):
        rng = np.random.default_rng(100 + i)
        n = months_back + months_fwd
        steps = rng.normal(mk["drift"] / 12, mk["vol"] / np.sqrt(12), n)
        idx = 100 * np.exp(np.cumsum(steps))
        idx = idx / idx[months_back - 1] * 100  # base 100 aujourd'hui
        for m in range(n):
            d = (pd.Timestamp(TODAY) - pd.DateOffset(months=months_back - 1 - m)).replace(day=1)
            v = idx[m]
            if m >= months_back and shock and fam in shock:
                v *= 1 + shock[fam] / 100
            rows.append(dict(family=fam, month=d, index=round(float(v), 2), kind="Historique (simulé)" if m < months_back else "Prévision (simulée)", market=mk["index"]))
    return pd.DataFrame(rows)


def cost_evolution(risk: pd.DataFrame, series: pd.DataFrame, year: int) -> float:
    """Variation du coût matière du produit entre janvier et décembre d'une année, d'après l'indice simulé."""
    mat_cost = (risk["est_cost"] / risk["process_factor"].replace(0, 1)).groupby(risk["family"]).sum()
    total = 0.0
    for fam, c in mat_cost.items():
        s = series.loc[(series["family"] == fam) & (series["month"].dt.year == year)].sort_values("month")
        if len(s) >= 2:
            total += c * (s["index"].iloc[-1] / s["index"].iloc[0] - 1)
    return round(float(total), 1)


# ---------------------------------------------------------------------------
# Registre de risques et couverture
# ---------------------------------------------------------------------------
def auto_risks(risk: pd.DataFrame, suppliers: pd.DataFrame, materials: pd.DataFrame) -> list[dict]:
    """Risques déduits automatiquement : composants critiques, contrats qui expirent, matières à risque."""
    out = []
    for _, r in risk.loc[risk["tier"] == "Critique"].iterrows():
        out.append(dict(id=f"A-{r['xml_id']}", title=f"Composant critique : {r['short']}", type="Composant", source="auto",
                        probability=r["supply_risk"] + 1, impact=min(5, 2 + int(r["est_cost"] > 50) + int(r["total_mass"] > 5)),
                        linked=r["short"], linked_id=r["xml_id"], family=r["family"], supplier_category=r["supplier_category"],
                        mitigation="", status="Ouvert"))
    for _, s in suppliers.loc[suppliers["primary"] & suppliers["contract_status"].isin(["Expiré", "Expire < 3 mois", "Expire < 6 mois"])].iterrows():
        cat_cost = float(risk.loc[risk["supplier_category"] == s["supplier_category"], "est_cost"].sum())
        out.append(dict(id=f"A-{s['supplier_id']}", title=f"Contrat {s['name']} : {s['contract_status'].lower()}", type="Fournisseur", source="auto",
                        probability=4 if s["months_left"] <= 3 else 3, impact=min(5, 1 + int(cat_cost / 60)),
                        linked=s["name"], linked_id=s["supplier_id"], family="", supplier_category=s["supplier_category"],
                        mitigation="Renégocier ou lancer une consultation", status="Ouvert"))
    for fam, mk in FAMILY_MARKET.items():
        exp = float((risk.loc[risk["family"] == fam, "est_cost"] / risk.loc[risk["family"] == fam, "process_factor"].replace(0, 1)).sum())
        if exp > 30 and mk["vol"] >= 0.18:
            out.append(dict(id=f"A-MKT-{fam}", title=f"Volatilité du prix : {fam}", type="Externe", source="auto",
                            probability=4, impact=min(5, 1 + int(exp / 40)), linked=fam, linked_id="", family=fam, supplier_category="",
                            mitigation="Couverture financière ou prix fixe", status="Ouvert"))
    return out


def risk_matrix(risks: list[dict]) -> np.ndarray:
    m = np.zeros((5, 5), dtype=int)
    for r in risks:
        if r.get("status") != "Clos":
            m[int(r["probability"]) - 1, int(r["impact"]) - 1] += 1
    return m


def hedging_options(risk: pd.DataFrame, materials: pd.DataFrame, suppliers: pd.DataFrame, family: str = "", supplier_category: str = "", xml_id: str = "") -> list[dict]:
    """Propositions de couverture : alternatives matière, autres fournisseurs, couverture financière."""
    opts = []
    exposure = 0.0
    if family:
        sub = risk.loc[risk["family"] == family]
        exposure = float((sub["est_cost"] / sub["process_factor"].replace(0, 1)).sum())
        mk = FAMILY_MARKET.get(family, {})
        if mk.get("hedge"):
            opts.append(dict(kind="Couverture financière", label=f"Contrat à terme (forward) sur {mk['index']}", detail=f"Figer le prix sur 12 mois pour ≈{exposure:.0f} € de matière par groupe motopropulseur ; coût = base + marge banque, pas de risque de base sur l'indice LME.", score=5))
            opts.append(dict(kind="Couverture financière", label=f"Futures {mk['index']} (3 à 15 mois)", detail="Couverture standardisée, appels de marge quotidiens ; adaptée si le volume annuel dépasse un lot (25 t).", score=4))
        else:
            opts.append(dict(kind="Contrat", label=f"Clause de prix fixe ou plafond avec le fournisseur ({mk.get('index', family)})", detail="Pas de marché à terme liquide pour cette famille : négocier un prix fixe 12 mois ou un tunnel d'indexation.", score=3))
        used = set(risk["material"])
        alt = materials.loc[(materials["family"] == family) & (~materials["material"].isin(used))]
        for _, a in alt.iterrows():
            opts.append(dict(kind="Matière alternative", label=a["material"], detail=f"{a['note']} · {a['price_eur_kg']} €/kg · risque {a['risk_label'].lower()} · CO₂ {a['co2_kg_per_kg']} kg/kg", score=4 - int(a["supply_risk"]) + 1))
    if supplier_category:
        cur = suppliers.loc[suppliers["supplier_category"] == supplier_category]
        for _, s in cur.loc[~cur["primary"]].iterrows():
            opts.append(dict(kind="Fournisseur alternatif", label=f"{s['name']} ({s['country']})", detail=f"{s['note']} · note {s['rating']}/5 · ESG {s['esg_index']} · délai de réponse {s['answer_time_days']} j · {s['contract_type']}", score=int(s["rating"])))
        if cur.loc[~cur["primary"]].empty:
            opts.append(dict(kind="Fournisseur alternatif", label=f"Aucune seconde source au catalogue pour « {supplier_category} »", detail="Lancer un sourcing : identifier et qualifier un second fournisseur (6 à 12 mois).", score=2))
        p = cur.loc[cur["primary"]]
        if not p.empty and p.iloc[0]["months_left"] <= 6:
            opts.append(dict(kind="Contrat", label=f"Prolonger le contrat {p.iloc[0]['name']} (échéance dans {p.iloc[0]['months_left']:.0f} mois)", detail="Renégocier avant l'échéance : volume engagé contre prix et stock de sécurité.", score=4))
    if xml_id:
        r = risk.loc[risk["xml_id"] == xml_id]
        if not r.empty and r.iloc[0]["kind"] == "Achat catalogue":
            opts.append(dict(kind="Stock", label="Stock de sécurité (pièce catalogue)", detail="Pièce standard : un stock tampon de 8 à 12 semaines couvre un délai fournisseur long sans engagement d'outillage.", score=3))
    return sorted(opts, key=lambda o: -o["score"])


# ---------------------------------------------------------------------------
# Mise à jour de la nomenclature : ajouts manuels
# ---------------------------------------------------------------------------
NEW_ID_PREFIX = "NEW_"


def apply_additions(base: PlmModel, additions: list[dict]) -> PlmModel:
    """Renvoie un modèle enrichi des composants ou sous-assemblages ajoutés à la main.

    Chaque ajout : {name, kind: part|assembly, parent_occ, material, mass_kg, qty, rev, unit_cost?, supplier_category?, description}.
    Les masses des parents sont augmentées ; la source est « saisie utilisateur » (xml_line = 0).
    """
    if not additions:
        if "cost_override" not in base.bom.columns:
            base.bom["cost_override"] = np.nan
            base.bom["origin"] = "XML"
        return base
    m = copy.deepcopy(base)
    bom = m.bom.copy()
    if "cost_override" not in bom.columns:
        bom["cost_override"] = np.nan
        bom["origin"] = "XML"
    rows = bom.to_dict("records")
    by_occ = {r["occ"]: r for r in rows}
    for n, a in enumerate(additions):
        parent = by_occ.get(a["parent_occ"])
        if parent is None:
            continue
        xml_id = f"{NEW_ID_PREFIX}{n + 1}"
        qty = int(a.get("qty", 1) or 1)
        for k in range(qty):
            occ = f"{parent['occ']}.n{n + 1}{chr(97 + k) if qty > 1 else ''}"
            sub = parent["short"] if parent["level"] == 0 else parent["subassembly"]
            row = dict(parent)
            row.update({
                "xml_id": xml_id, "product_line": 0, "xml_line": 0, "inst": f"NEW_INST_{n + 1}_{k + 1}", "occ": occ,
                "part_number": a.get("part_number") or f"new-{n + 1:04d}", "name": a["name"], "short": a["name"], "description": a.get("description", ""),
                "rev": a.get("rev", "A") or "A", "rev_index": ord((a.get("rev", "A") or "A")[0]) - 64, "maturity": "IN_WORK", "lifecycle": "Saisie utilisateur",
                "created": TODAY.isoformat(), "modified": TODAY.isoformat(), "owner": "utilisateur", "organization": "Fournisseur",
                "mass_kg": float(a.get("mass_kg", 0) or 0), "material": a.get("material", "") if a["kind"] == "part" else "",
                "instance_name": f"{a['name']}.{k + 1}", "parent_occ": parent["occ"], "parent_short": parent["short"], "level": int(parent["level"]) + 1,
                "role": a["kind"], "role_label": ROLE_LABEL[a["kind"]], "children": 0, "subassembly": sub,
                "path": parent["path"] + " › " + a["name"], "rep3d": "", "cost_override": a.get("unit_cost") if a.get("unit_cost") else np.nan,
                "origin": "Ajout manuel",
            })
            rows.append(row)
            by_occ[occ] = row
            # masse des parents
            p = parent
            while p is not None:
                p["mass_kg"] = round(p["mass_kg"] + row["mass_kg"], 3)
                p["children"] = p.get("children", 0) + (1 if p is parent else 0)
                p = by_occ.get(p["parent_occ"])
        if parent["role"] == "part":  # une pièce qui reçoit des enfants devient un assemblage
            parent["role"], parent["role_label"] = "assembly", ROLE_LABEL["assembly"]
    m.bom = pd.DataFrame(rows)
    prods = m.bom.drop_duplicates("xml_id").set_index("xml_id", drop=False)
    m.products = prods[[c for c in base.products.columns if c in prods.columns]]
    return m
