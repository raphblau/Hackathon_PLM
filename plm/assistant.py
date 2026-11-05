"""Assistant conversationnel sur les données PLM, avec traçabilité des sources.

Deux modes :
- **Claude** (si une clé API est disponible) : Claude choisit parmi des outils qui
  interrogent la nomenclature ; chaque résultat d'outil porte ses références XML
  (identifiant d'objet + numéro de ligne), que la réponse doit citer.
- **Hors ligne** : un routeur par mots-clés appelle les mêmes outils, pour que la démo
  fonctionne sans clé. Les réponses sont alors plus mécaniques.

Les outils sont la seule porte d'entrée vers les données : l'assistant ne reçoit jamais
le XML brut, ce qui garantit que chaque chiffre vient d'une requête vérifiable.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from . import analytics
from .parser import PlmModel

MODEL_ID = "claude-opus-5-5"

SYSTEM_PROMPT = """Tu es l'assistant « PLM Intelligence » d'un fournisseur de moteurs électriques.
Tu réponds en français à des ingénieurs et acheteurs, à partir d'un export XPDMXML reçu de l'OEM
(groupe motopropulseur ECAR 410 kW avant). Tu n'as accès aux données QUE par les outils fournis.

Règles :
- Appelle toujours au moins un outil avant de donner un chiffre. N'invente jamais une valeur.
- Chaque chiffre ou fait doit être suivi de sa référence entre crochets, telle que renvoyée par
  l'outil : [ID_54 · L.1047] pour un objet Product, [occ 148.152 · L.2946] pour une occurrence.
- Les coûts, risques d'approvisionnement, CO₂ et fournisseurs viennent d'un référentiel matières
  d'EXEMPLE (pas de l'OEM). Dis-le quand tu les utilises.
- Termine par une section « Sources » listant les objets consultés (une ligne par objet).
- Sois concis : réponse courte, puis détails sous forme de liste si utile. Pas de tableau Markdown
  de plus de 8 lignes ; propose plutôt d'affiner la question.
"""


# ---------------------------------------------------------------------------
# Outils : fonctions pures sur le modèle, résultats JSON avec références XML
# ---------------------------------------------------------------------------
@dataclass
class ToolCall:
    name: str
    args: dict
    result: dict | list
    refs: list[str] = field(default_factory=list)


def _ref_product(row) -> str:
    return f"[{row['xml_id']} · L.{int(row['product_line'])}]"


def _ref_occ(row) -> str:
    return f"[occ {row['occ']} · L.{int(row['xml_line'])}]"


def _records(df: pd.DataFrame, cols: list[str], limit: int = 25) -> list[dict]:
    d = df[cols].head(limit)
    return json.loads(d.to_json(orient="records", force_ascii=False))


class Toolbox:
    def __init__(self, model: PlmModel, materials: pd.DataFrame, risk: pd.DataFrame | None = None):
        self.m = model
        self.mats = materials
        self.risk = risk if risk is not None else analytics.part_risk(model, materials)

    # -- définitions pour l'API ------------------------------------------------
    def definitions(self) -> list[dict]:
        return [
            {
                "name": "search_components",
                "description": "Recherche des composants par nom, référence, matière ou mot-clé. Renvoie les occurrences correspondantes avec révision, maturité, masse, matière et références XML.",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Texte à chercher (insensible à la casse)."}}, "required": ["query"], "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "get_component",
                "description": "Fiche complète d'un article (par xml_id, ex. ID_54) ou d'une occurrence (par occ, ex. 148.152) : propriétés, chemin dans l'assemblage, enfants directs, où il est utilisé.",
                "input_schema": {"type": "object", "properties": {"id": {"type": "string", "description": "xml_id (ID_n) ou occ (ex. 148.152)."}}, "required": ["id"], "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "bom_overview",
                "description": "Vue d'ensemble : produit racine, sous-ensembles de niveau 1 avec masse et nombre de composants, indicateurs globaux (masse, références, maturité, matières, coût estimé).",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "critical_components",
                "description": "Les composants les plus critiques (score 0-100 : risque matière, coût, masse, occurrences, révisions). Peut filtrer par sous-ensemble.",
                "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Nombre de lignes (défaut 8)."}, "subassembly": {"type": "string", "description": "Optionnel : EMOTOR FRONT, REDUCER FRONT ou HOUSING FRONT EDRIVE."}}, "required": ["limit", "subassembly"], "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "materials_and_suppliers",
                "description": "Masse, coût estimé et risque par matière, et dépendances par catégorie de fournisseur (référentiel d'exemple).",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "change_impact",
                "description": "Impact du remplacement de la matière d'une pièce (xml_id) par une autre matière du référentiel : masse, coût, CO2, fournisseur, assemblages à réviser. Utiliser list_materials pour les noms exacts.",
                "input_schema": {"type": "object", "properties": {"xml_id": {"type": "string"}, "new_material": {"type": "string", "description": "Nom exact d'une matière du référentiel."}}, "required": ["xml_id", "new_material"], "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "price_shock",
                "description": "Surcoût si le prix d'une famille de matières (Aluminium, Cuivre, Acier, Acier électrique, Fonte, Élastomère, Polymère) varie de pct %.",
                "input_schema": {"type": "object", "properties": {"family": {"type": "string"}, "pct": {"type": "number"}}, "required": ["family", "pct"], "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "list_materials",
                "description": "Liste des matières du référentiel (nom exact, famille, prix €/kg, densité, CO2, risque, fournisseur) et lesquelles sont utilisées dans la nomenclature.",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "strict": True,
            },
            {
                "name": "data_quality",
                "description": "Contrôles de qualité de l'export : maturité, doublons de noms, géométries d'aide, anomalies de nommage, cohérence des masses.",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "strict": True,
            },
        ]

    # -- exécution -------------------------------------------------------------
    def run(self, name: str, args: dict) -> ToolCall:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return ToolCall(name, args, {"error": f"Outil inconnu : {name}"})
        try:
            result, refs = fn(**args)
        except Exception as e:  # l'erreur est renvoyée au modèle, pas levée
            result, refs = {"error": str(e)}, []
        return ToolCall(name, args, result, refs)

    def _occ_rows(self, df: pd.DataFrame, limit=25):
        cols = ["occ", "xml_id", "short", "name", "instance_name", "role_label", "rev", "maturity",
                "material", "mass_kg", "subassembly", "path", "xml_line", "product_line"]
        rows = _records(df, cols, limit)
        for r in rows:
            r["ref"] = f"[{r['xml_id']} · L.{r['product_line']}] [occ {r['occ']} · L.{r['xml_line']}]"
        refs = [f"{r['xml_id']} · L.{r['product_line']}" for r in rows]
        return rows, sorted(set(refs))

    def t_search_components(self, query: str):
        q = query.lower().strip()
        b = self.m.bom
        hay = (b["short"] + " " + b["name"] + " " + b["material"] + " " + b["part_number"] + " " + b["instance_name"]).str.lower()
        hits = b.loc[hay.str.contains(re.escape(q)) & (b["role"] != "geometry")]
        rows, refs = self._occ_rows(hits)
        return {"query": query, "matches": len(hits), "shown": len(rows), "rows": rows}, refs

    def t_get_component(self, id: str):
        b = self.m.bom
        if id.startswith("ID_"):
            occs = b.loc[b["xml_id"] == id]
        else:
            occs = b.loc[b["occ"] == id]
        if occs.empty:
            return {"error": f"Aucun composant {id}"}, []
        o = occs.iloc[0]
        children = b.loc[b["parent_occ"] == o["occ"]]
        used = self.m.where_used(o["xml_id"])
        risk = self.risk.loc[self.risk["xml_id"] == o["xml_id"]]
        fiche = {
            "xml_id": o["xml_id"], "part_number": o["part_number"], "name": o["name"], "short": o["short"],
            "description": o["description"], "role": o["role_label"], "rev": o["rev"], "rev_index": int(o["rev_index"]),
            "maturity": o["maturity"], "lifecycle": o["lifecycle"], "material": o["material"], "mass_kg": o["mass_kg"],
            "owner": o["owner"], "project": o["project"], "modified": o["modified"], "rep3d": o["rep3d"],
            "subassembly": o["subassembly"], "qty_in_product": len(used), "ref": _ref_product(o),
        }
        if not risk.empty:
            r = risk.iloc[0]
            fiche.update({"est_cost_eur (hypothèse)": float(r["est_cost"]), "supply_risk": r["risk_label"],
                          "supplier_category (hypothèse)": r["supplier_category"], "criticality_score": int(r["score"]), "tier": r["tier"]})
        occ_rows, _ = self._occ_rows(occs)
        ch_rows, _ = self._occ_rows(children)
        anc = [{"occ": a["occ"], "short": a["short"], "rev": a["rev"], "ref": _ref_product(a)} for a in self.m.ancestors(o["occ"])]
        refs = [f"{o['xml_id']} · L.{int(o['product_line'])}"] + [f"occ {r['occ']} · L.{r['xml_line']}" for r in occ_rows]
        return {"component": fiche, "occurrences": occ_rows, "children": ch_rows, "parents_to_root": anc}, refs

    def t_bom_overview(self):
        b = self.m.bom
        root = b.loc[b["level"] == 0].iloc[0]
        subs = []
        for _, s in b.loc[b["level"] == 1].iterrows():
            desc = b.loc[b["path"].str.startswith(s["path"]) & (b["role"] == "part")]
            subs.append({"occ": s["occ"], "short": s["short"], "rev": s["rev"], "mass_kg": s["mass_kg"],
                         "part_occurrences": len(desc), "part_refs": desc["xml_id"].nunique(), "ref": _ref_product(s)})
        k = analytics.kpis(self.m, self.risk)
        k = {kk: (float(v) if hasattr(v, "item") else v) for kk, v in k.items()}
        refs = [f"{root['xml_id']} · L.{int(root['product_line'])}"] + [f"{s['ref'][1:-1]}" for s in subs]
        return {"root": {"short": root["short"], "rev": root["rev"], "mass_kg": root["mass_kg"], "maturity": root["maturity"], "ref": _ref_product(root)},
                "subassemblies": subs, "kpis": k, "note": "est_cost et critical/watch reposent sur le référentiel matières d'exemple."}, refs

    def t_critical_components(self, limit: int = 8, subassembly: str = ""):
        r = self.risk
        if subassembly:
            r = r.loc[r["subassembly"].str.contains(subassembly, case=False)]
        rows = _records(r, ["xml_id", "short", "rev", "subassembly", "material", "qty", "total_mass", "est_cost", "risk_label", "supplier_category", "score", "tier", "unstable", "product_line"], limit or 8)
        for x in rows:
            x["ref"] = f"[{x['xml_id']} · L.{x['product_line']}]"
        return {"formula": "40 % risque matière + 30 % coût + 20 % masse + 10 % occurrences, +5 pts par révision au-delà de A (max +15)", "rows": rows,
                "note": "Coûts, risques et fournisseurs = hypothèses du référentiel d'exemple."}, [f"{x['xml_id']} · L.{x['product_line']}" for x in rows]

    def t_materials_and_suppliers(self):
        bm = analytics.by_material(self.risk)
        bs = analytics.by_supplier(self.risk)
        return {"by_material": json.loads(bm.to_json(orient="records", force_ascii=False)),
                "by_supplier": json.loads(bs.to_json(orient="records", force_ascii=False)),
                "note": "Référentiel matières d'exemple (data/materials_ref.csv). Les matières et masses viennent du XML (champ Material, V_WCG_Mass de chaque Product)."}, ["materials_ref.csv (hypothèses)"]

    def t_change_impact(self, xml_id: str, new_material: str):
        ci = analytics.change_impact(self.m, self.risk, self.mats, xml_id, new_material)
        imp = json.loads(ci["impacted"].to_json(orient="records", force_ascii=False))
        for x in imp:
            x["ref"] = f"[occ {x['occ']} · L.{x['xml_line']}]"
        out = {k: (float(v) if hasattr(v, "item") else v) for k, v in ci.items() if k not in ("part", "from", "to", "impacted")}
        out.update({"part": ci["part"]["short"], "part_ref": f"[{xml_id} · L.{int(ci['part']['product_line'])}]", "material_from": ci["from"]["material"], "material_to": new_material,
                    "supplier_from": ci["from"]["supplier_category"], "supplier_to": ci["to"]["supplier_category"],
                    "risk_from": analytics.RISK_LABEL[int(ci["from"]["supply_risk"])], "risk_to": analytics.RISK_LABEL[int(ci["to"]["supply_risk"])],
                    "impacted_objects": imp, "assumption": "volume constant ; prix et densités du référentiel d'exemple"})
        return out, [f"{xml_id} · L.{int(ci['part']['product_line'])}"] + [f"occ {x['occ']} · L.{x['xml_line']}" for x in imp]

    def t_price_shock(self, family: str, pct: float):
        ps = analytics.price_shock(self.risk, family, pct)
        rows = _records(ps, ["xml_id", "short", "material", "est_cost", "extra_cost", "product_line"], 15)
        for x in rows:
            x["ref"] = f"[{x['xml_id']} · L.{x['product_line']}]"
        return {"family": family, "pct": pct, "total_extra_cost": round(float(ps["extra_cost"].sum()), 1),
                "share_of_total_cost": round(float(ps["extra_cost"].sum() / self.risk["est_cost"].sum()), 4), "rows": rows,
                "note": "Le choc s'applique à la part matière du coût (coût / facteur procédé)."}, [f"{x['xml_id']} · L.{x['product_line']}" for x in rows]

    def t_list_materials(self):
        used = set(self.m.bom["material"])
        m = self.mats.copy()
        m["in_bom"] = m["material"].isin(used)
        return {"materials": json.loads(m.to_json(orient="records", force_ascii=False))}, ["materials_ref.csv (hypothèses)"]

    def t_data_quality(self):
        return {"checks": analytics.quality_checks(self.m), "export_header": json.loads(self.m.info.to_json(orient="records"))}, ["InfoHeader (L.3-19)"]


# ---------------------------------------------------------------------------
# Mode Claude
# ---------------------------------------------------------------------------
def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def ask_claude(toolbox: Toolbox, history: list[dict], question: str, max_iterations: int = 8) -> tuple[str, list[ToolCall], list[dict]]:
    """Boucle agentique : Claude appelle les outils jusqu'à produire une réponse.

    Renvoie (texte, appels d'outils, nouvel historique à conserver).
    """
    import anthropic

    client = anthropic.Anthropic()
    messages = history + [{"role": "user", "content": question}]
    calls: list[ToolCall] = []

    def create(**kw):
        # Repli serveur en cas de refus de sécurité (classifiers) ; ignoré si le SDK ne le connaît pas.
        try:
            return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
        except TypeError:
            return client.messages.create(**kw)

    for _ in range(max_iterations):
        response = create(
            model=MODEL_ID,
            max_tokens=4096,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=toolbox.definitions(),
            messages=messages,
        )
        if response.stop_reason == "refusal":
            return "La demande a été refusée par les garde-fous du modèle.", calls, history
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                call = toolbox.run(block.name, dict(block.input))
                calls.append(call)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(call.result, ensure_ascii=False, default=str)[:60000],
                                "is_error": isinstance(call.result, dict) and "error" in call.result})
        messages.append({"role": "user", "content": results})

    text = "".join(b.text for b in messages[-1]["content"] if getattr(b, "type", "") == "text")
    return text, calls, messages


# ---------------------------------------------------------------------------
# Mode hors ligne : routeur par mots-clés vers les mêmes outils
# ---------------------------------------------------------------------------
def _fmt_refs(refs: list[str]) -> str:
    return "\n\n**Sources**\n" + "\n".join(f"- {r}" for r in sorted(set(refs))[:12])


# --- vocabulaire -------------------------------------------------------------
_FAMILY_WORDS = {
    "Aluminium": ["alu", "alluminium", "aluminium", "aluminum"], "Cuivre": ["cuivre", "copper", "bobinage", "fil émaillé"],
    "Acier électrique": ["acier électrique", "tôle magnétique", "tole magnetique", "electrical steel", "m235"],
    "Acier": ["acier", "steel", "20mncr5", "100cr6", "42crmo4", "16mncr5", "visserie", "vis "],
    "Fonte": ["fonte", "cast iron"], "Élastomère": ["élastomère", "elastomere", "fkm", "joint"], "Polymère": ["polyamide", "plastique", "pa66", "polymère"],
    "Magnésium": ["magnésium", "magnesium"],
}
_SUB_WORDS = {"EMOTOR FRONT": ["moteur", "emotor", "e-motor", "stator"], "REDUCER FRONT": ["réducteur", "reducteur", "reducer", "différentiel", "engrenage"],
              "HOUSING FRONT EDRIVE": ["carter", "housing", "boîtier", "boitier"]}
_PART_SYN = {"noyau du stator": "STATOR Core", "noyau stator": "STATOR Core", "carter gauche": "left housing", "carter droit": "right housing", "carter moteur": "motor", "support de transmission": "Transmission support",
             "logement de roulement": "bearing housing", "noyau stator": "STATOR Core", "noyau du stator": "STATOR Core", "bobines": "STATOR Coils", "bobine": "STATOR Coil",
             "arbre grande vitesse": "HSH", "arbre intermédiaire": "ISH", "roue dentée": "GEAR_WHEEL", "roues dentées": "GEAR_WHEEL", "pignon": "GEAR_WHEEL",
             "roulement": "BEA", "roulements": "BEA", "vis": "Screw", "joint spi": "SPY joint", "joint": "SPY joint", "tuyau": "90 degrees pipe", "pipe": "90 degrees pipe",
             "refroidissement": "MACHINE COOLING", "différentiel": "DIFFERENTIAL", "axe": "PIN", "plaque": "plate bearing motor", "dent": "STATOR Core Tooth"}
_NEW_MAT_WORDS = {"magnésium": "Magnesium alloy AZ91D (HPDC)", "magnesium": "Magnesium alloy AZ91D (HPDC)", "aluminium recyclé": "Recycled aluminium AlSi9Cu3 (≥75 % secondary)",
                  "recyclé": "Recycled aluminium AlSi9Cu3 (≥75 % secondary)", "fil aluminium": "Aluminium winding wire EC-Al (enamelled)", "hnbr": "HNBR elastomer (steel-reinforced)",
                  "pa6": "Polyamide PA6-GF30", "18crnimo": "Case-hardening steel 18CrNiMo7-6", "16mncr5": "Case-hardening steel 16MnCr5", "alsi10mg": "Aluminium alloy EN AC-AlSi10Mg (cast)",
                  "6082": "Aluminium alloy EN AW-6082-T6"}


def _norm(q: str) -> str:
    q = q.lower().replace("’", "'").replace("l'", "").replace("d'", "")
    return re.sub(r"\s+", " ", q)


def _find_family(q: str) -> str | None:
    for fam, words in _FAMILY_WORDS.items():
        if any(w in q for w in words):
            return fam
    return None


def _find_sub(q: str) -> str | None:
    for sub, words in _SUB_WORDS.items():
        if any(w in q for w in words):
            return sub
    return None


def _find_part(toolbox: Toolbox, q: str) -> list[str]:
    """Noms courts des pièces reconnues dans la question (synonymes français puis noms exacts, puis approchant)."""
    import difflib
    hits = []
    for fr, en in _PART_SYN.items():
        if re.search(r"\b" + re.escape(fr) + r"\b", q):
            hits.append(en)
    shorts = toolbox.m.bom.loc[toolbox.m.bom["role"] != "geometry", "short"].unique().tolist()
    for sname in shorts:
        if sname.lower() in q and sname not in hits:
            hits.append(sname)
    if not hits:
        words = [w for w in re.findall(r"[a-z0-9_]{4,}", q) if w not in _STOP]
        for w in words:
            close = difflib.get_close_matches(w, [x.lower() for x in shorts], n=1, cutoff=0.8)
            if close:
                hits.append(next(x for x in shorts if x.lower() == close[0]))
    return sorted(hits, key=len, reverse=True)  # le nom le plus précis d'abord


def _match_rows(toolbox: Toolbox, key: str):
    b = toolbox.m.bom.loc[toolbox.m.bom["role"] != "geometry"]
    exact = b.loc[b["short"].str.lower() == key.lower()]
    return exact if not exact.empty else b.loc[b["short"].str.contains(re.escape(key), case=False)]


def ask_offline(toolbox: Toolbox, question: str) -> tuple[str, list[ToolCall]]:
    """Routeur hors ligne : détecte l'intention et l'entité, appelle les outils, cite les sources."""
    q = _norm(question)
    fam, sub, parts = _find_family(q), _find_sub(q), _find_part(toolbox, q)
    has = lambda *ws: any(w in q for w in ws)
    risk = toolbox.risk

    # 1. impact d'un changement de matière
    if has("impact", "remplac", "substitu", "passe en", "passer en", "changer", "change ") and (parts or fam):
        new_mat = next((m for w, m in _NEW_MAT_WORDS.items() if w in q), None)
        if not new_mat:
            new_mat = next((m for m in toolbox.mats["material"] if m.lower() in q), None)
        if not parts:
            return (f"Quelle pièce voulez-vous modifier ? Exemples : « impact si le carter gauche passe en magnésium », « remplacer le noyau stator par du 18CrNiMo ».", [])
        rows = _match_rows(toolbox, parts[0])
        rows = rows.loc[rows["role"] == "part"]
        if rows.empty:
            return (f"Je ne trouve pas la pièce « {parts[0]} ».", [])
        xml_id = rows.iloc[0]["xml_id"]
        if not new_mat:
            cur = toolbox.mats.set_index("material").loc[rows.iloc[0]["material"], "family"] if rows.iloc[0]["material"] in set(toolbox.mats["material"]) else ""
            alts = toolbox.mats.loc[(toolbox.mats["family"] == cur) & (~toolbox.mats["material"].isin(set(toolbox.m.bom["material"])))]["material"].tolist()
            return (f"Vers quelle matière ? Pour **{rows.iloc[0]['short']}** ({rows.iloc[0]['material']}) le référentiel propose : " + ", ".join(alts or ["magnésium", "aluminium recyclé"]) + ".", [])
        c = toolbox.run("change_impact", {"xml_id": xml_id, "new_material": new_mat})
        r = c.result
        if "error" in r:
            return f"Impossible de calculer : {r['error']}", [c]
        sup = "fournisseur inchangé" if not r["supplier_change"] else f"changement de fournisseur : {r['supplier_from']} → {r['supplier_to']}"
        imp = "\n".join(f"- {x['short']} ({x['role_label']}) rév. {x['rev']} → {x['next_rev']} {x['ref']}" for x in r["impacted_objects"])
        txt = (f"**{r['part']}** {r['part_ref']} : {r['material_from']} → {r['material_to']} (volume constant)\n\n"
               f"- Masse : {r['d_mass']:+.3f} kg ({r['d_mass_pct']:+.0%}) sur {r['qty']} occurrence(s)\n- Coût estimé : {r['d_cost']:+.1f} € ({r['d_cost_pct']:+.0%})\n"
               f"- CO₂ matière : {r['d_co2']:+.1f} kg\n- Risque appro : {r['risk_from']} → {r['risk_to']} ; {sup}\n"
               + ("- ⚠ Changement de famille de matière : la géométrie doit être revue.\n" if r["family_change"] else "")
               + f"\nObjets PLM à réviser :\n{imp}\n\n*Hypothèses : {r['assumption']}.*")
        return txt + _fmt_refs(c.refs), [c]

    # 2. choc de prix
    if has("choc", "hausse", "augment", "baisse", "renchér", "%") and fam:
        m = re.search(r"([+-]?\d+)\s*%", q)
        pct = float(m.group(1)) if m else 30.0
        if has("baisse") and pct > 0:
            pct = -pct
        c = toolbox.run("price_shock", {"family": fam, "pct": pct})
        r = c.result
        lines = "\n".join(f"- {x['short']} : {x['extra_cost']:+.1f} € {x['ref']}" for x in r["rows"][:8])
        txt = f"Si le prix de la famille **{fam}** varie de **{pct:+.0f} %**, surcoût estimé **{r['total_extra_cost']:+.1f} €** par groupe motopropulseur ({r['share_of_total_cost']:.1%} du coût pièces estimé).\n\nPièces touchées :\n{lines}\n\n*{r['note']} Prix = référentiel d'exemple.*"
        return txt + _fmt_refs(c.refs), [c]

    # 3. masse / coût / liste par famille ou matière
    if fam and not parts and (has("poids", "masse", "kg", "pèse", "combien", "coût", "cout", "prix", "quelles pièces", "quels composants", "liste", "pièces en", "composants en") or len(q.split()) <= 6):
        c = toolbox.run("materials_and_suppliers", {})
        bm = [x for x in c.result["by_material"] if x["family"] == fam]
        parts_f = risk.loc[risk["family"] == fam].sort_values("total_mass", ascending=False)
        if not bm:
            return f"Aucune pièce en {fam} dans la nomenclature.", [c]
        tot_m, tot_c = sum(x["mass"] for x in bm), sum(x["cost"] for x in bm)
        txt = f"**{fam}** : **{tot_m:.2f} kg** sur {toolbox.m.bom.loc[toolbox.m.bom['level'] == 0, 'mass_kg'].iloc[0]:.1f} kg au total ({tot_m / toolbox.m.bom.loc[toolbox.m.bom['level'] == 0, 'mass_kg'].iloc[0]:.0%}), {len(parts_f)} références, coût estimé {tot_c:.0f} €.\n\nPar matière :\n"
        txt += "\n".join(f"- {x['material']} : {x['mass']:.2f} kg · {x['cost']:.0f} € · risque {x['risk_label'].lower()}" for x in bm)
        refs = [f"{r.xml_id} · L.{int(r.product_line)}" for r in parts_f.itertuples()]
        txt += "\n\nPièces :\n" + "\n".join(f"- {r.short} (rév. {r.rev}) · {r.qty} × {r.unit_mass:.3f} kg = {r.total_mass:.2f} kg [{r.xml_id} · L.{int(r.product_line)}]" for r in parts_f.head(10).itertuples())
        txt += "\n\n*Masses et matières : champs V_WCG_Mass et Material des objets Product. Coûts : référentiel d'exemple.*"
        return txt + _fmt_refs(refs), [c]

    # 4. criticité / risques
    if has("critique", "risque", "critical", "fragile", "sensible", "surveiller"):
        c = toolbox.run("critical_components", {"limit": 6, "subassembly": sub or ""})
        lines = [f"- **{r['short']}** (rév. {r['rev']}) · score {r['score']} · {r['tier']} · {r['material']} · {r['est_cost']} € est. {r['ref']}" for r in c.result["rows"]]
        txt = "Composants les plus critiques" + (f" ({sub})" if sub else "") + " :\n" + "\n".join(lines) + f"\n\n*Formule : {c.result['formula']}. Coûts et risques = référentiel d'exemple.*"
        return txt + _fmt_refs(c.refs), [c]

    # 5. fiche d'une pièce (masse, matière, où utilisée, révision…)
    if parts:
        rows = _match_rows(toolbox, parts[0])
        if not rows.empty:
            if len(rows["xml_id"].unique()) > 1 and has("combien", "liste", "quels", "quelles", "où", "ou sont"):
                c = toolbox.run("search_components", {"query": parts[0]})
                rr = c.result["rows"]
                txt = f"{c.result['matches']} occurrence(s) pour « {parts[0]} », masse cumulée {sum(x['mass_kg'] for x in rr):.2f} kg :\n" + "\n".join(
                    f"- **{x['short']}** rév. {x['rev']} · {x['mass_kg']} kg · {x['material'] or '—'} · {x['path']} {x['ref']}" for x in rr[:12])
                return txt + _fmt_refs(c.refs), [c]
            c = toolbox.run("get_component", {"id": rows.iloc[0]["xml_id"]})
            f = c.result["component"]
            txt = (f"**{f['name']}** {f['ref']} · {f['role']}\n\n- Référence {f['part_number']} · révision {f['rev']} · {f['maturity']}\n- Matière : {f['material'] or '—'} · masse unitaire {f['mass_kg']} kg · {f['qty_in_product']} occurrence(s) → {f['mass_kg'] * f['qty_in_product']:.3f} kg\n"
                   f"- Sous-ensemble : {f['subassembly']} · chemin : " + " › ".join(a["short"] for a in reversed(c.result["parents_to_root"])) + f" › {f['short']}\n")
            if "criticality_score" in f:
                txt += f"- Criticité {f['criticality_score']}/100 ({f['tier']}) · coût estimé {f['est_cost_eur (hypothèse)']} € · risque appro {f['supply_risk']} · fournisseur {f['supplier_category (hypothèse)']} *(hypothèses)*\n"
            if c.result["children"]:
                txt += "- Composants : " + ", ".join(x["short"] for x in c.result["children"][:12]) + "\n"
            return txt + _fmt_refs(c.refs), [c]

    # 6. matières et fournisseurs (général)
    if has("matière", "matiere", "fournisseur", "material", "supplier", "dépendance"):
        c = toolbox.run("materials_and_suppliers", {})
        bm, bs = c.result["by_material"][:8], c.result["by_supplier"][:6]
        txt = "Masse par matière :\n" + "\n".join(f"- {x['material']} : {x['mass']:.2f} kg · {x['cost']:.0f} € est. · risque {x['risk_label'].lower()}" for x in bm)
        txt += "\n\nDépendances fournisseurs (part du coût estimé) :\n" + "\n".join(f"- {x['supplier_category']} ({x['kind']}) : {x['cost_share']:.0%} · risque max {x['risk_label'].lower()}" for x in bs)
        txt += "\n\n*Masses et matières : XML. Coûts, risques, fournisseurs : référentiel d'exemple.*"
        return txt + _fmt_refs(c.refs), [c]

    # 7. qualité
    if has("qualité", "qualite", "anomal", "libér", "maturit", "doublon", "fiab", "erreur"):
        c = toolbox.run("data_quality", {})
        txt = "Contrôles qualité de l'export :\n" + "\n".join(f"- **{x['level']}** · {x['title']} : {x['count']}" + (f" / {x['total']}" if 'total' in x else "") + f" — {x['text']}" for x in c.result["checks"])
        return txt + _fmt_refs(c.refs), [c]

    # 8. vue d'ensemble / sous-ensemble
    if has("sous-ensemble", "sous ensemble", "structure", "composé", "compose", "ensemble", "overview", "masse totale", "poids total", "combien de") or sub:
        c = toolbox.run("bom_overview", {})
        r = c.result
        if sub:
            s_ = next(x for x in r["subassemblies"] if x["short"] == sub)
            occ_rows = toolbox.m.bom.loc[(toolbox.m.bom["subassembly"] == sub) & (toolbox.m.bom["role"] == "part")]
            txt = f"**{sub}** rév. {s_['rev']} · {s_['mass_kg']} kg · {s_['part_refs']} références / {s_['part_occurrences']} occurrences {s_['ref']}\n\nPrincipales pièces :\n" + "\n".join(
                f"- {x.short} · {x.mass_kg} kg · {x.material or '—'} [{x.xml_id} · L.{int(x.product_line)}]" for x in occ_rows.drop_duplicates("xml_id").sort_values("mass_kg", ascending=False).head(10).itertuples())
            return txt + _fmt_refs(c.refs), [c]
        subs = "\n".join(f"- **{s_['short']}** rév. {s_['rev']} · {s_['mass_kg']} kg · {s_['part_refs']} références / {s_['part_occurrences']} occurrences {s_['ref']}" for s_ in r["subassemblies"])
        k = r["kpis"]
        txt = (f"**{r['root']['short']}** rév. {r['root']['rev']}, {r['root']['mass_kg']} kg, {r['root']['maturity']} {r['root']['ref']}\n\nSous-ensembles :\n{subs}\n\n"
               f"{int(k['part_refs'])} références de pièces, {int(k['part_occ'])} occurrences, {int(k['materials'])} matières, {k['released_pct']:.0%} d'objets libérés.")
        return txt + _fmt_refs(c.refs), [c]

    # 9. recherche libre
    key = " ".join(w for w in re.findall(r"[a-z0-9_]{3,}", q) if w not in _STOP)[:40]
    c = toolbox.run("search_components", {"query": key})
    rows = c.result.get("rows", [])
    if not rows:
        return ("Je n'ai pas compris la question. Je sais répondre sur : la structure (« quels sont les sous-ensembles ? »), une pièce (« fiche du carter gauche »), "
                "une matière (« quel est le poids de l'aluminium ? »), la criticité (« composants critiques du réducteur »), un changement (« impact si le carter gauche passe en magnésium »), "
                "un choc de prix (« +30 % sur le cuivre »), les fournisseurs et la qualité des données."), [c]
    lines = "\n".join(f"- **{r['short']}** ({r['role_label']}) rév. {r['rev']} · {r['mass_kg']} kg · {r['material'] or '—'} · {r['path']} {r['ref']}" for r in rows[:10])
    return f"{c.result['matches']} occurrence(s) trouvée(s) pour « {key} » :\n{lines}" + _fmt_refs(c.refs), [c]


_STOP = {"le", "la", "les", "de", "du", "des", "un", "une", "si", "en", "au", "aux", "et", "ou", "quel", "quelle", "quels", "quelles", "est", "sont", "que", "qui", "pour",
         "dans", "avec", "sur", "poids", "masse", "combien", "donne", "moi", "info", "fiche", "détail", "detail", "pièce", "piece", "composant", "cherche", "trouve", "liste", "sous"}
