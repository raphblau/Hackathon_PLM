"""PLM Intelligence — application Streamlit (dashboard + assistant) sur un export XPDMXML.

Lancer :  streamlit run app.py
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from plm import analytics, assistant, extras, icons, parser

ROOT = Path(__file__).parent
XML_PATH = ROOT / "data" / "XPDMXML_ECAR_PLM.xml"
MAT_PATH = ROOT / "data" / "materials_ref.csv"
SUP_PATH = ROOT / "data" / "suppliers.csv"

C_BLUE, C_MUTED, C_BAD, C_WARN, C_OK = "#1f6feb", "#b7c4d9", "#d13438", "#c77d00", "#1a9c5b"
SUB_COLORS = {"EMOTOR FRONT": "#1f6feb", "REDUCER FRONT": "#7a5af8", "HOUSING FRONT EDRIVE": "#0f9d8a"}
TIER_CHIP = {"Critique": "bad", "À surveiller": "warn", "Faible": "ok"}
RISK_CHIP = {"Élevé": "bad", "Moyen": "warn", "Faible": "ok"}

st.set_page_config(page_title="PLM Intelligence · ECAR e-drive", page_icon="⚙️", layout="wide", initial_sidebar_state="expanded")
st.markdown(f"<style>{(ROOT / 'assets' / 'style.css').read_text()}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Données (mises en cache)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Lecture de l'export XPDMXML…")
def load_all(xml_bytes: bytes | None):
    path = XML_PATH
    if xml_bytes:
        path = ROOT / "data" / "_uploaded.xml"
        path.write_bytes(xml_bytes)
    try:
        model = parser.load(path)
    except Exception as e:  # fichier invalide : on revient à l'export d'origine en le signalant
        st.session_state["load_error"] = f"{type(e).__name__} : {e}"
        path = XML_PATH
        model = parser.load(path)
    mats = analytics.load_materials(MAT_PATH)
    risk = analytics.part_risk(model, mats)
    xml_lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    return model, mats, risk, xml_lines


def xml_snippet(lines: list[str], start: int, max_lines: int = 22) -> str:
    """Les lignes XML d'un élément, pour afficher la source brute."""
    out, i = [], start - 1
    while i < len(lines) and len(out) < max_lines:
        out.append(f"{i + 1:>5}  {lines[i]}")
        if i > start - 1 and lines[i].strip().startswith("</") and lines[i].startswith("    <") is False and len(lines[i]) - len(lines[i].lstrip()) <= 4:
            break
        i += 1
    return "\n".join(out)


def fmt_kg(v) -> str:
    return f"{v:,.3f}".replace(",", " ") if abs(v) < 1 else f"{v:,.2f}".replace(",", " ")


def fmt_eur(v) -> str:
    return f"{v:,.0f} €".replace(",", " ") if abs(v) >= 100 else f"{v:,.1f} €".replace(",", " ")


def chip(text, kind="info") -> str:
    return f'<span class="chip {kind}">{html.escape(str(text))}</span>'


_WIDGETS: list = []


def widget_open(title: str, note: str = ""):
    """Ouvre une carte « widget » (conteneur bordé, style plateforme PLM) ; fermer avec widget_close()."""
    c = st.container(border=True)
    c.__enter__()
    _WIDGETS.append(c)
    st.markdown(f'<div class="plm-wtitle">{html.escape(title)}</div>' + (f'<p class="plm-wnote">{note}</p>' if note else ""), unsafe_allow_html=True)


def widget_close():
    _WIDGETS.pop().__exit__(None, None, None)


def delta_tile(col, label, value, delta="", good=None, sub=""):
    """Tuile avec variation colorée : good=True vert, False rouge, None neutre."""
    cls = "ok" if good is True else "bad" if good is False else "neutral"
    col.markdown(
        f'<div class="plm-kpi"><div class="label">{html.escape(label)}</div><div class="value">{value}</div>'
        f'<div class="sub"><span class="delta {cls}">{html.escape(delta)}</span> {html.escape(sub)}</div></div>', unsafe_allow_html=True)


def kpi(col, label, value, sub="", sample=False, unit=""):
    col.markdown(
        f'<div class="plm-kpi"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{value}<small>{unit}</small></div>'
        f'<div class="sub{" sample" if sample else ""}">{sub}</div></div>',
        unsafe_allow_html=True,
    )


def plotly_base(fig: go.Figure, height=320) -> go.Figure:
    top = fig.layout.margin.t if fig.layout.margin and fig.layout.margin.t else 8
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=top, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Segoe UI, Helvetica Neue, Arial", size=12, color="#1c2430"),
        hoverlabel=dict(bgcolor="#fff", bordercolor="#d7dee8", font=dict(color="#1c2430")),
        legend=dict(orientation="h", y=-0.14, x=0, font=dict(size=11)),
    )
    fig.update_xaxes(gridcolor="#e6ebf2", zerolinecolor="#d7dee8", showline=False)
    fig.update_yaxes(gridcolor="#e6ebf2", zerolinecolor="#d7dee8", showline=False)
    return fig


def room(fig: go.Figure, values, axis="x", factor=1.22) -> go.Figure:
    """Laisse de la place aux étiquettes hors des barres."""
    lo, hi = min(0, float(min(values))), float(max(values))
    (fig.update_xaxes if axis == "x" else fig.update_yaxes)(range=[lo * factor, hi * factor])
    return fig


# ---------------------------------------------------------------------------
# État partagé
# ---------------------------------------------------------------------------
if "xml_upload" not in st.session_state:
    st.session_state.xml_upload = None
BASE_MODEL, MATS, _, XML_LINES = load_all(st.session_state.xml_upload)
SUPPLIERS = extras.load_suppliers(SUP_PATH)
st.session_state.setdefault("weights", dict(analytics.DEFAULT_WEIGHTS))
st.session_state.setdefault("additions", [])
st.session_state.setdefault("risks_manual", [])
st.session_state.setdefault("edits", {})
MODEL = extras.apply_additions(BASE_MODEL, st.session_state.additions, st.session_state.edits)
RISK = analytics.part_risk(MODEL, MATS, st.session_state.weights, SUPPLIERS)
BOM = MODEL.bom
KPI = analytics.kpis(MODEL, RISK)
SUBS = ["Tous"] + BOM.loc[BOM["level"] == 1, "short"].tolist()
_PALETTE = ["#1f6feb", "#7a5af8", "#0f9d8a", "#c77d00", "#d13438", "#2f9e44", "#e8590c", "#5d6b7e"]
for _i, _n in enumerate(SUBS[1:]):
    SUB_COLORS.setdefault(_n, _PALETTE[_i % len(_PALETTE)])
MATURITY_VIOL = analytics.maturity_consistency(MODEL)
LLM = assistant.llm_backend()
st.session_state.setdefault("sel_occ", "148.152")
st.session_state.setdefault("ci_part", "ID_54")
st.session_state.setdefault("ci_mat", "Magnesium alloy AZ91D (HPDC)")
st.session_state.setdefault("chat", [])


def topbar(app: str, ctx: str):
    mode = "Assistant : " + LLM["label"]
    st.markdown(
        f'<div class="plm-topbar"><div class="compass"></div><div><div class="app">{html.escape(app)}</div>'
        f'<div class="ctx">{html.escape(ctx)}</div></div><div class="spacer"></div>'
        f'<span class="pill">Projet ECAR_PLM</span><span class="pill">{html.escape(MODEL.source_name)}</span><span class="pill">{mode}</span></div>',
        unsafe_allow_html=True,
    )


def sub_filter(key: str) -> str | None:
    c1, c2 = st.columns([1, 3])
    sub = c1.selectbox("Sous-ensemble", SUBS, key=key)
    return None if sub == "Tous" else sub


def filtered_risk(sub: str | None) -> pd.DataFrame:
    return RISK if sub is None else RISK.loc[RISK["subassembly"] == sub]


def goto_impact(xml_id: str):
    st.session_state.ci_part = xml_id
    st.session_state["nav_target"] = "impact"


# ---------------------------------------------------------------------------
# Page : Product Explorer
# ---------------------------------------------------------------------------
def page_explorer():
    topbar("Product Explorer", "Nomenclature du groupe motopropulseur avant 410 kW, telle qu'exportée par l'OEM")
    root = BOM.loc[BOM["level"] == 0].iloc[0]
    st.markdown(
        f'<p class="plm-lede"><b>{html.escape(root["short"])}</b> rév. {root["rev"]} · {fmt_kg(root["mass_kg"])} kg · {KPI["part_refs"]} références de pièces en {KPI["part_occ"]} occurrences, '
        f'réparties dans {KPI["assemblies"]} assemblages. {KPI["released_pct"]:.0%} des objets sont libérés : toute la définition est encore en travail.</p>',
        unsafe_allow_html=True,
    )
    c = st.columns(6)
    kpi(c[0], "Masse totale", fmt_kg(KPI["total_mass"]), f"Produit racine, L.{KPI['root_line']}", unit=" kg")
    kpi(c[1], "Références de pièces", KPI["part_refs"], f"{KPI['part_occ']} occurrences")
    kpi(c[2], "Objets Product", KPI["products"], f"{KPI['assemblies']} assemblages")
    kpi(c[3], "Références libérées", f"{KPI['released_pct']:.0%}", "Maturité RELEASED")
    kpi(c[4], "Matières distinctes", KPI["materials"], f"{KPI['high_risk_mass_pct']:.0%} de la masse à risque élevé", sample=True)
    kpi(c[5], "Pièces critiques", KPI["critical"], f"+ {KPI['watch']} à surveiller", sample=True)
    st.write("")

    sub = sub_filter("explorer_sub")
    left, right = st.columns([7, 5], gap="medium")

    with left:
        widget_open("Où se trouve la masse", "Chaque anneau est un niveau de la nomenclature ; la taille d'un secteur est sa masse. Cliquez pour zoomer.")
        tree = BOM.loc[BOM["role"] != "geometry"].copy()
        if sub:
            tree = tree.loc[(tree["level"] == 0) | (tree["subassembly"] == sub)]
        tree["value"] = tree["mass_kg"]
        if sub:  # la racine ne doit pas dépasser la somme de la branche affichée
            tree.loc[tree["level"] == 0, "value"] = tree.loc[tree["level"] == 1, "mass_kg"].sum()
        tree["color"] = tree["subassembly"].map(SUB_COLORS).fillna("#8a97ab")
        fig = go.Figure(go.Sunburst(
            ids=tree["occ"], labels=tree["short"], parents=tree["parent_occ"].where(tree["parent_occ"] != "", None), values=tree["value"],
            branchvalues="total", marker=dict(colors=tree["color"], line=dict(color="#fff", width=1.5)),
            customdata=tree[["rev", "material", "mass_kg", "maturity"]].values,
            hovertemplate="<b>%{label}</b><br>%{customdata[2]:.3f} kg · rév. %{customdata[0]}<br>%{customdata[1]}<br>%{customdata[3]}<extra></extra>",
            insidetextorientation="radial", maxdepth=3,
        ))
        plotly_base(fig, 420)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        widget_close()

        widget_open("Nomenclature (BOM)", "Regroupée pour s'y retrouver : dépliez un groupe, puis sélectionnez une ligne pour ses propriétés et sa source XML.")
        f1, f2, f3 = st.columns([3, 2, 1])
        q = f1.text_input("Rechercher", placeholder="Pièce, matière, référence, instance…", label_visibility="collapsed")
        group_mode = f2.selectbox("Regrouper par", ["Arborescence", "Type de composant", "Matière", "Fournisseur", "Maturité"], label_visibility="collapsed")
        show_geo = f3.toggle("Rep. 3D", value=False)
        tbl = BOM if show_geo else BOM.loc[BOM["role"] != "geometry"]
        if sub:
            tbl = tbl.loc[(tbl["level"] == 0) | (tbl["subassembly"] == sub)]
        if q:
            hay = (tbl["short"] + " " + tbl["material"] + " " + tbl["part_number"] + " " + tbl["instance_name"]).str.lower()
            tbl = tbl.loc[hay.str.contains(q.lower(), regex=False)]
        bad_parents, bad_children = set(MATURITY_VIOL["parent_occ"]), set(MATURITY_VIOL["child_occ"])
        sup_of = RISK.set_index("xml_id")["supplier"]

        def coherence(o):
            return "⚠ enfants moins mûrs" if o in bad_parents else "⚠ parent plus mûr" if o in bad_children else "✓"

        def table(rows: pd.DataFrame, key: str, indent=True):
            view = pd.DataFrame({
                "Composant": rows.apply(lambda r: ((" " * (r["level"] - 1)) if indent else "") + ("▸ " if r["role"] == "assembly" else "") + r["short"] + (" [3D]" if r["role"] == "geometry" else ""), axis=1),
                "Rév.": rows["rev"], "Maturité": rows["maturity"], "Cohérence": rows["occ"].map(coherence),
                "Matière": rows["material"].str.replace(r"\s*\(.*?\)", "", regex=True), "Masse (kg)": rows["mass_kg"], "Origine": rows["origin"], "occ": rows["occ"],
            })
            ev = st.dataframe(view, hide_index=True, height=min(60 + 35 * len(view), 420), on_select="rerun", selection_mode="single-row", width="stretch",
                              column_config={"occ": None, "Masse (kg)": st.column_config.NumberColumn(format="%.3f"), "Composant": st.column_config.TextColumn(width="medium")}, key=key)
            if ev.selection.rows:
                st.session_state.sel_occ = view.iloc[ev.selection.rows[0]]["occ"]

        if group_mode == "Arborescence":
            root_rows = tbl.loc[tbl["level"] == 0]
            if not root_rows.empty:
                table(root_rows, "bom_root", indent=False)
            for s1 in BOM.loc[BOM["level"] == 1].itertuples():
                branch = tbl.loc[tbl["subassembly"] == s1.short]
                if branch.empty:
                    continue
                n_parts = int((branch["role"] == "part").sum())
                with st.expander(f"{s1.short} · rév. {s1.rev} · {fmt_kg(s1.mass_kg)} kg · {n_parts} pièces · {coherence(s1.occ)}", expanded=(sub == s1.short) or bool(q)):
                    direct = branch.loc[(branch["level"] == 1) | (branch["parent_occ"] == s1.occ) & (branch["role"] != "assembly")]
                    sub_asm = branch.loc[(branch["role"] == "assembly") & (branch["level"] >= 2)]
                    if not direct.empty:
                        table(direct, f"bom_{s1.occ}")
                    for s2 in sub_asm.itertuples():
                        inner = branch.loc[branch["path"].str.startswith(s2.path + " ›") | (branch["occ"] == s2.occ)]
                        st.markdown(f"**↳ {html.escape(s2.short)}** · rév. {s2.rev} · {fmt_kg(s2.mass_kg)} kg · {coherence(s2.occ)}", unsafe_allow_html=True)
                        table(inner, f"bom_{s2.occ}")
        else:
            parts_only = tbl.loc[tbl["role"] != "assembly"].copy()
            if group_mode == "Type de composant":
                parts_only["grp"] = parts_only.apply(lambda r: icons.LABEL[icons.kind_of(r["short"], r["role"])], axis=1)
            elif group_mode == "Matière":
                parts_only["grp"] = parts_only["material"].str.replace(r"\s*\(.*?\)", "", regex=True).replace("", "Sans matière")
            elif group_mode == "Fournisseur":
                parts_only["grp"] = parts_only["xml_id"].map(sup_of).fillna("Non attribué").replace("", "Non attribué")
            else:
                parts_only["grp"] = parts_only["maturity"]
            for grp, rows in sorted(parts_only.groupby("grp"), key=lambda kv: -kv[1]["mass_kg"].sum()):
                with st.expander(f"{grp} · {len(rows)} occurrence(s) · {fmt_kg(rows['mass_kg'].sum())} kg", expanded=bool(q)):
                    table(rows, f"bom_g_{abs(hash(grp))}", indent=False)
        widget_close()

    with right:
        occ = st.session_state.sel_occ
        if not (BOM["occ"] == occ).any():
            occ = BOM.iloc[1]["occ"]
        r = MODEL.row(occ)
        anc = MODEL.ancestors(occ)
        crumbs = " › ".join(html.escape(a["short"]) for a in reversed(anc))
        widget_open("Propriétés du composant")
        st.markdown(f'<div class="plm-crumbs">{crumbs} › <b>{html.escape(r["short"])}</b></div>', unsafe_allow_html=True)
        ic, tx = st.columns([1, 4])
        ic.markdown(icons.svg(r["short"], r["role"], 88), unsafe_allow_html=True)
        tx.markdown(f"**{html.escape(r['name'])}**<br>{chip(r['role_label'], 'fill')} {chip(r['maturity'], 'warn' if r['maturity'] != 'RELEASED' else 'ok')} {chip(icons.LABEL[icons.kind_of(r['short'], r['role'])], 'info')}<br><span style='font-size:.72rem;color:#8a97ab'>Pictogramme illustratif : l'export ne contient pas la géométrie 3D.</span>", unsafe_allow_html=True)
        used = MODEL.where_used(r["xml_id"])
        rk = RISK.loc[RISK["xml_id"] == r["xml_id"]]
        props = [
            ("Référence", r["part_number"]), ("Instance", r["instance_name"]), ("Révision", f"{r['rev']} (index {r['rev_index']})"),
            ("Cycle de vie", r["lifecycle"]), ("Matière", r["material"] or "—"), ("Masse unitaire", f"{fmt_kg(r['mass_kg'])} kg"),
            ("Quantité", f"{len(used)} dans le produit"), ("Description", r["description"] or "—"), ("Rep. 3D", r["rep3d"] or "—"),
            ("Modifié le", r["modified"].replace("T", " ").replace(".0Z", " UTC")), ("Propriétaire", f"{r['owner']} · {r['organization']}"),
        ]
        if not rk.empty:
            k = rk.iloc[0]
            props += [("Coût estimé*", f"{fmt_eur(k['est_cost'])} ({k['cost_source'].lower()})"), ("Risque appro*", k["risk_label"]),
                      ("Fournisseur*", f"{k['supplier'] or '—'} · {k['supplier_category']}" + (f" · contrat : {k['contract_status'].lower()}" if k["contract_status"] else "")),
                      ("Criticité*", f"{k['score']} / 100 · {k['tier']}")]
        props.insert(4, ("Cohérence maturité", analytics.maturity_status(MODEL, r["occ"])))
        st.markdown('<div class="plm-props">' + "".join(f'<div class="k">{html.escape(a)}</div><div class="v">{html.escape(str(b))}</div>' for a, b in props) + "</div>", unsafe_allow_html=True)
        if r.get("origin") == "Ajout manuel":
            st.markdown('<div class="plm-src">Source : <b>saisie utilisateur</b> (page « Mise à jour de la nomenclature »), absent de l\'export OEM.' + (" &nbsp;* hypothèses du référentiel matières." if not rk.empty else "") + "</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                f'<div class="plm-src">Source : objet Product <code>{r["xml_id"]}</code> ligne {r["product_line"]} · instance <code>{r["inst"]}</code> ligne {r["xml_line"]} du fichier {html.escape(MODEL.source_name)}.'
                + (" &nbsp;* hypothèses du référentiel matières." if not rk.empty else "") + "</div>", unsafe_allow_html=True)
            with st.expander("Voir la source XML"):
                st.code(xml_snippet(XML_LINES, int(r["product_line"])), language="xml")
                if r["inst"] != "ROOT":
                    st.code(xml_snippet(XML_LINES, int(r["xml_line"]), 20), language="xml")
        if r["role"] == "part":
            st.button("Évaluer un changement de matière", on_click=goto_impact, args=(r["xml_id"],), type="primary", width="stretch")
        widget_close()

        widget_open("Où est utilisée cette pièce", f"{len(used)} occurrence(s) de la référence {html.escape(r['part_number'])}.")
        st.dataframe(
            used[["instance_name", "parent_short", "path", "xml_line"]].rename(columns={"instance_name": "Instance", "parent_short": "Assemblage parent", "path": "Chemin", "xml_line": "Ligne XML"}),
            hide_index=True, width="stretch", height=min(60 + 35 * len(used), 260),
        )
        widget_close()

    a, b = st.columns(2, gap="medium")
    with a:
        widget_open("Révisions des références de pièces", "Une révision D signifie trois évolutions majeures déjà réalisées : la définition n'est pas stable.")
        rd = RISK.groupby("rev").size().reset_index(name="refs")
        fig = px.bar(rd, x="rev", y="refs", text="refs", color_discrete_sequence=[C_BLUE])
        fig.update_traces(textposition="outside", marker_line_width=0, width=0.5)
        fig.update_layout(xaxis_title=None, yaxis_title=None, showlegend=False)
        room(fig, rd["refs"], "y")
        st.plotly_chart(plotly_base(fig, 230), width="stretch", config={"displayModeBar": False})
        widget_close()
    with b:
        widget_open("Versions qui coexistent sous un même nom", "Objets distincts, de révisions et masses différentes, utilisés simultanément.")
        multi = RISK.groupby("name")["rev"].nunique()
        mr = RISK.loc[RISK["name"].isin(multi[multi > 1].index)].sort_values(["name", "rev"])
        st.dataframe(mr[["short", "rev", "unit_mass", "part_number", "subassembly", "product_line"]].rename(
            columns={"short": "Nom", "rev": "Rév.", "unit_mass": "Masse (kg)", "part_number": "Référence", "subassembly": "Sous-ensemble", "product_line": "Ligne XML"}),
            hide_index=True, width="stretch", height=230)
        widget_close()


# ---------------------------------------------------------------------------
# Page : Product Intelligence
# ---------------------------------------------------------------------------
def page_intelligence():
    topbar("Product Intelligence", "Composants critiques, matières à risque, dépendances fournisseurs")
    st.markdown(
        '<p class="plm-lede">Le paquet OEM ne contient ni coût ni fournisseur. Les analyses ci-dessous croisent les matières et masses du XML avec un '
        '<b>référentiel matières d\'exemple</b> (<code>data/materials_ref.csv</code>) : prix, facteur procédé, CO₂, risque d\'approvisionnement, catégorie de fournisseur. À remplacer par les données achats.</p>',
        unsafe_allow_html=True,
    )
    sub = sub_filter("intel_sub")
    r = filtered_risk(sub)
    bm, bs = analytics.by_material(r), analytics.by_supplier(r)
    c = st.columns(5)
    kpi(c[0], "Coût pièces estimé", fmt_eur(r["est_cost"].sum()), "Masse × prix × facteur procédé", sample=True)
    kpi(c[1], "Pièces critiques", int((r["tier"] == "Critique").sum()), f"+ {int((r['tier'] == 'À surveiller').sum())} à surveiller", sample=True)
    kpi(c[2], "Masse à risque élevé", f"{bm.loc[bm['supply_risk'] == 3, 'mass'].sum() / bm['mass'].sum():.0%}", "Tôle magnétique, cuivre, FKM", sample=True)
    kpi(c[3], "Achat catalogue", fmt_eur(r.loc[r["kind"] == "Achat catalogue", "est_cost"].sum()), f"{int(r.loc[r['kind'] == 'Achat catalogue', 'qty'].sum())} pièces : roulements, vis, joints", sample=True)
    kpi(c[4], "Révisions instables", int(r["unstable"].sum()), "Références en révision C ou plus")
    st.write("")

    a, b = st.columns(2, gap="medium")
    with a:
        widget_open("Masse par matière", "Toutes occurrences comprises. En rouge, les matières à risque d'approvisionnement élevé.")
        fig = px.bar(bm.sort_values("mass"), x="mass", y="material", orientation="h", color=bm["supply_risk"].eq(3).map({True: "Risque élevé", False: "Autre"}),
                     color_discrete_map={"Risque élevé": C_BAD, "Autre": C_BLUE}, text=bm.sort_values("mass")["mass"].map(lambda v: f"{v:.1f} kg"),
                     custom_data=["cost", "risk_label", "refs"])
        fig.update_traces(textposition="outside", marker_line_width=0, hovertemplate="<b>%{y}</b><br>%{x:.2f} kg · %{customdata[0]:.0f} € est.<br>risque %{customdata[1]} · %{customdata[2]} réf.<extra></extra>")
        fig.update_layout(xaxis_title=None, yaxis_title=None, legend_title=None, yaxis=dict(tickfont=dict(size=11)))
        room(fig, bm["mass"], factor=1.4)
        st.plotly_chart(plotly_base(fig, 380), width="stretch", config={"displayModeBar": False})
        widget_close()
    with b:
        widget_open("Coût estimé par matière", "Le cuivre et la tôle magnétique du stator pèsent plus en coût qu'en masse.")
        bc = bm.sort_values("cost")
        fig = px.bar(bc, x="cost", y="material", orientation="h", color=bc["supply_risk"].eq(3).map({True: "Risque élevé", False: "Autre"}),
                     color_discrete_map={"Risque élevé": C_BAD, "Autre": C_BLUE}, text=bc["cost"].map(lambda v: f"{v:.0f} €"), custom_data=["mass", "risk_label"])
        fig.update_traces(textposition="outside", marker_line_width=0, hovertemplate="<b>%{y}</b><br>%{x:.0f} € est. · %{customdata[0]:.2f} kg<br>risque %{customdata[1]}<extra></extra>")
        fig.update_layout(xaxis_title=None, yaxis_title=None, legend_title=None, yaxis=dict(tickfont=dict(size=11)))
        room(fig, bc["cost"], factor=1.4)
        st.plotly_chart(plotly_base(fig, 380), width="stretch", config={"displayModeBar": False})
        widget_close()

    w = st.session_state.weights
    tot = max(1, w["w_risk"] + w["w_cost"] + w["w_mass"] + w["w_qty"])
    widget_open("Criticité des composants",
                f"Score sur 100 : {w['w_risk'] / tot:.0%} risque matière, {w['w_cost'] / tot:.0%} coût, {w['w_mass'] / tot:.0%} masse, {w['w_qty'] / tot:.0%} occurrences ; "
                f"+{w['rev_points']} pts par révision au-delà de A (max +{w['rev_cap']}), +{w['contract_points']} pts si le contrat fournisseur expire sous {w['contract_months']} mois, "
                f"+{w['esg_points']} pts si ESG fournisseur < {w['esg_floor']}. Critique ≥ {w['crit']}, à surveiller ≥ {w['watch']}. Modifiable dans « Paramètres de criticité ».")
    view = r[["short", "rev", "subassembly", "material", "qty", "total_mass", "est_cost", "risk_label", "supplier", "contract_status", "kind", "score", "tier", "xml_id"]].copy()
    view["rev"] = view.apply(lambda x: f"{x['rev']} ⚠" if x["rev"] >= "C" else x["rev"], axis=1)
    view = view.rename(columns={"short": "Composant", "rev": "Rév.", "subassembly": "Sous-ensemble", "material": "Matière", "qty": "Qté", "total_mass": "Masse (kg)",
                                "est_cost": "Coût est. (€)", "risk_label": "Risque appro", "supplier": "Fournisseur", "contract_status": "Contrat", "kind": "Type", "score": "Score", "tier": "Niveau"})
    ev = st.dataframe(view, hide_index=True, width="stretch", height=420, on_select="rerun", selection_mode="single-row", key="risk_table",
                      column_config={"xml_id": None, "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
                                     "Masse (kg)": st.column_config.NumberColumn(format="%.2f"), "Coût est. (€)": st.column_config.NumberColumn(format="%.0f")})
    if ev.selection.rows:
        picked = view.iloc[ev.selection.rows[0]]
        st.button(f"Simuler un changement de matière sur « {picked['Composant']} »", on_click=goto_impact, args=(picked["xml_id"],), type="primary")
    widget_close()

    a, b = st.columns([5, 7], gap="medium")
    with a:
        widget_open("Choc de prix matière", "Que se passe-t-il si une famille de matières renchérit ? Le choc s'applique à la part matière du coût (coût ÷ facteur procédé).")
        fam = st.selectbox("Famille", sorted(MATS["family"].unique()), index=sorted(MATS["family"].unique()).index("Aluminium"))
        pct = st.slider("Variation du prix", -50, 100, 30, 5, format="%+d %%")
        fam_label = fam
        ps = analytics.price_shock(r, fam, pct)
        total = ps["extra_cost"].sum()
        m1, m2 = st.columns(2)
        delta_tile(m1, "Surcoût par groupe motopropulseur", f"{total:+.1f} €", "", good=total <= 0, sub="hypothèses référentiel")
        delta_tile(m2, "Part du coût pièces estimé", f"{total / r['est_cost'].sum():+.1%}" if r["est_cost"].sum() else "—", "", sub=f"{len(ps)} références exposées")
        widget_close()
    with b:
        widget_open("Pièces les plus exposées au choc", f"Famille {html.escape(fam)}, variation {pct:+d} %.")
        if ps.empty:
            st.info("Aucune pièce de cette famille dans la sélection.")
        else:
            top = ps.head(8).sort_values("extra_cost")
            fig = px.bar(top, x="extra_cost", y="short", orientation="h", text=top["extra_cost"].map(lambda v: f"{v:+.1f} €"), color_discrete_sequence=[C_WARN if pct > 0 else C_OK])
            fig.update_traces(textposition="outside", marker_line_width=0)
            fig.update_layout(xaxis_title=None, yaxis_title=None, showlegend=False)
            room(fig, top["extra_cost"], factor=1.3)
            st.plotly_chart(plotly_base(fig, 300), width="stretch", config={"displayModeBar": False})
        widget_close()

    widget_open("Concentration des achats par fournisseur",
                "À quoi sert ce tableau : savoir chez qui l'argent et le risque sont concentrés. Chaque pièce est rattachée au fournisseur principal de sa catégorie (catalogue data/suppliers.csv). "
                "Une ligne avec une forte part du coût, un contrat qui expire et pas de seconde source est une dépendance à traiter en priorité (voir « Risques & couverture »).")
    alt = SUPPLIERS.loc[~SUPPLIERS["primary"]].groupby("supplier_category")["name"].apply(lambda x: ", ".join(x))
    g = r.groupby(["supplier", "supplier_category", "kind"], as_index=False).agg(refs=("xml_id", "count"), qty=("qty", "sum"), cost=("est_cost", "sum"), risk=("supply_risk", "max"), crit=("tier", lambda t: int((t == "Critique").sum())))
    g["cost_share"] = g["cost"] / r["est_cost"].sum()
    g["Seconde source"] = g["supplier_category"].map(alt).fillna("aucune")
    g["Contrat"] = g["supplier"].map(SUPPLIERS.set_index("name")["contract_status"]).fillna("—")
    g["risk_label"] = g["risk"].map(analytics.RISK_LABEL)
    g["Priorité"] = ((g["cost_share"] > 0.1).astype(int) + g["Contrat"].str.startswith("Expire").astype(int) + (g["Seconde source"] == "aucune").astype(int) + (g["risk"] == 3).astype(int)).map({0: "—", 1: "Basse", 2: "Moyenne", 3: "Haute", 4: "Haute"})
    g = g.sort_values("cost", ascending=False)
    sv = g.rename(columns={"supplier": "Fournisseur", "supplier_category": "Catégorie", "kind": "Type", "refs": "Réf.", "qty": "Pièces", "cost": "Coût est. (€)", "cost_share": "Part du coût", "risk_label": "Risque matière", "crit": "Pièces critiques"}).drop(columns=["risk"])
    st.dataframe(sv[["Fournisseur", "Catégorie", "Type", "Réf.", "Pièces", "Coût est. (€)", "Part du coût", "Pièces critiques", "Risque matière", "Contrat", "Seconde source", "Priorité"]], hide_index=True, width="stretch",
                 column_config={"Part du coût": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"), "Coût est. (€)": st.column_config.NumberColumn(format="%.0f")})
    st.caption("Priorité = nombre de signaux parmi : part du coût > 10 %, contrat qui expire, pas de seconde source, matière à risque élevé.")
    widget_close()

    # ---- Marché matières (simulation) -----------------------------------------
    st.markdown('<p class="plm-eyebrow" style="margin-top:8px">Marché matières · simulation</p>', unsafe_allow_html=True)
    series = extras.price_series(MATS, shock={fam: pct} if pct else None)
    ev26, ev27 = extras.cost_evolution(r, series, 2026), extras.cost_evolution(r, series, 2027)
    a, b, d = st.columns([4, 4, 4], gap="medium")
    with a:
        widget_open("Décomposition du coût par famille", "Part de chaque famille de matière dans le coût pièces estimé.")
        fam = r.groupby("family")["est_cost"].sum().sort_values(ascending=False).reset_index()
        fig = px.pie(fam, names="family", values="est_cost", hole=0.55, color_discrete_sequence=["#1f6feb", "#7a5af8", "#0f9d8a", "#c77d00", "#d13438", "#5d6b7e", "#2f9e44", "#e8590c"])
        fig.update_traces(textinfo="percent", textposition="inside", textfont_size=11, hovertemplate="<b>%{label}</b><br>%{value:.0f} € · %{percent}<extra></extra>", marker=dict(line=dict(color="#fff", width=1.5)))
        fig.update_layout(legend=dict(orientation="v", y=0.5, x=1.02, font=dict(size=11)), margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(plotly_base(fig, 280), width="stretch", config={"displayModeBar": False})
        widget_close()
    with b:
        widget_open("Évolution du coût matière", "Variation du coût matière du produit entre janvier et décembre, d'après l'indice simulé par famille.")
        delta_tile(st, "Janvier → décembre 2026 (historique simulé)", f"{ev26:+.1f} €", "", good=ev26 <= 0, sub="par groupe motopropulseur")
        st.write("")
        delta_tile(st, "Janvier → décembre 2027 (prévision simulée)", f"{ev27:+.1f} €", "", good=ev27 <= 0, sub=f"avec le choc {fam_label} {pct:+d} %" if pct else "sans choc")
        widget_close()
    with d:
        widget_open("Exposition matière par famille", "Part matière du coût (coût ÷ facteur procédé) : ce qui est réellement exposé aux marchés.")
        ex = (r["est_cost"] / r["process_factor"].replace(0, 1)).groupby(r["family"]).sum().sort_values().reset_index(name="exposure")
        fig = px.bar(ex, x="exposure", y="family", orientation="h", text=ex["exposure"].map(lambda v: f"{v:.0f} €"), color_discrete_sequence=[C_BLUE])
        fig.update_traces(textposition="outside", marker_line_width=0)
        fig.update_layout(xaxis_title=None, yaxis_title=None, showlegend=False)
        room(fig, ex["exposure"], factor=1.3)
        st.plotly_chart(plotly_base(fig, 280), width="stretch", config={"displayModeBar": False})
        widget_close()
    widget_open("Prévision des indices de prix matière", f"Base 100 = aujourd'hui. Trait plein : historique simulé ; pointillé : prévision simulée (dérive et volatilité par famille, hypothèses d'exemple). Le choc {fam_label} {pct:+d} % s'applique à la prévision.")
    fams = st.multiselect("Familles", sorted(series["family"].unique()), default=["Aluminium", "Cuivre", "Acier électrique", "Acier"], label_visibility="collapsed")
    sel = series.loc[series["family"].isin(fams)]
    fig = px.line(sel, x="month", y="index", color="family", line_dash="kind", line_dash_map={"Historique (simulé)": "solid", "Prévision (simulée)": "dot"}, markers=True,
                  color_discrete_sequence=["#1f6feb", "#d13438", "#7a5af8", "#0f9d8a", "#c77d00", "#5d6b7e", "#2f9e44", "#e8590c"])
    fig.add_vline(x=pd.Timestamp(extras.TODAY).replace(day=1).timestamp() * 1000, line_dash="dash", line_color="#1c2430")
    fig.update_traces(marker_size=5, line_width=2)
    fig.update_layout(xaxis_title=None, yaxis_title="Indice (base 100)", legend_title=None)
    st.plotly_chart(plotly_base(fig, 340), width="stretch", config={"displayModeBar": False})
    widget_close()


# ---------------------------------------------------------------------------
# Page : Change Impact
# ---------------------------------------------------------------------------
def page_impact():
    topbar("Change Impact Analysis", "Conséquences d'un changement de matière : masse, coût, CO₂, fournisseurs, révisions")
    st.markdown('<p class="plm-lede">Le calcul suppose une pièce de même volume : la masse change avec la densité, le coût avec le prix et le procédé. Chaque assemblage parent devra passer à la révision suivante.</p>', unsafe_allow_html=True)
    parts = RISK.sort_values(["subassembly", "short"])
    labels = {row.xml_id: f"{row.short} · rév. {row.rev} · {row.subassembly}" for row in parts.itertuples()}
    mats = MATS["material"].tolist()
    c1, c2 = st.columns(2)
    xml_id = c1.selectbox("Pièce à modifier", list(labels), format_func=labels.get, index=list(labels).index(st.session_state.ci_part) if st.session_state.ci_part in labels else 0)
    new_mat = c2.selectbox("Nouvelle matière", mats, index=mats.index(st.session_state.ci_mat) if st.session_state.ci_mat in mats else 0,
                           format_func=lambda m: f"{m} · {MATS.set_index('material').loc[m, 'family']}")
    st.session_state.ci_part, st.session_state.ci_mat = xml_id, new_mat
    try:
        ci = analytics.change_impact(MODEL, RISK, MATS, xml_id, new_mat)
    except ValueError as e:
        st.error(str(e))
        return
    p, a, b = ci["part"], ci["from"], ci["to"]
    ic, tx = st.columns([1, 11])
    ic.markdown(icons.svg(p["short"], "part", 64), unsafe_allow_html=True)
    tx.markdown(f"**{html.escape(p['short'])}** · {icons.LABEL[icons.kind_of(p['short'])]} (pictogramme illustratif)<br>Matière actuelle : **{html.escape(a['material'])}** ({a['family']}) · fournisseur actuel : {html.escape(a['supplier_category'])} · {ci['qty']} occurrence(s) · masse unitaire {fmt_kg(ci['old_unit_mass'])} kg → {fmt_kg(ci['new_unit_mass'])} kg", unsafe_allow_html=True)

    c = st.columns(5)
    delta_tile(c[0], "Masse (toutes occurrences)", f"{ci['d_mass']:+.2f} kg", f"{ci['d_mass_pct']:+.0%}", good=ci["d_mass"] < 0, sub="sur la pièce")
    delta_tile(c[1], "Coût estimé", f"{ci['d_cost']:+.1f} €", f"{ci['d_cost_pct']:+.0%}", good=ci["d_cost"] < 0, sub="hypothèses référentiel")
    delta_tile(c[2], "CO₂ matière", f"{ci['d_co2']:+.1f} kg", "", good=ci["d_co2"] < 0, sub="à la production")
    ra, rb = int(a["supply_risk"]), int(b["supply_risk"])
    delta_tile(c[3], "Risque appro", f"{analytics.RISK_LABEL[ra]} → {analytics.RISK_LABEL[rb]}", "", good=None if ra == rb else rb < ra, sub="approvisionnement")
    delta_tile(c[4], "Assemblages à réviser", int((ci["impacted"]["role_label"] == "Assemblage").sum()), "", sub=f"{len(ci['impacted'])} objets PLM touchés")
    st.write("")
    if ci["supplier_change"]:
        st.warning(f"**Changement de fournisseur nécessaire** : {a['supplier_category']} → {b['supplier_category']}. Prévoir consultation, homologation et nouvel outillage.")
    else:
        st.success(f"Même catégorie de fournisseur ({a['supplier_category']}) : le changement peut probablement rester chez le fournisseur actuel, sous réserve de requalification.")
    if ci["family_change"]:
        st.warning(f"**Changement de famille de matière** ({a['family']} → {b['family']}) : le calcul à volume constant ne suffit pas, la géométrie doit être revue par le bureau d'études.")

    widget_open("Schéma de dépendance : qui et quoi est touché", "De gauche à droite : la matière et le fournisseur actuels, la pièce modifiée, les assemblages qui la contiennent jusqu'au produit, puis les acteurs qui doivent intervenir. L'épaisseur des flux suit le coût estimé.")
    imp = ci["impacted"].sort_values("level", ascending=False)
    chain = [x for x in imp.itertuples()]  # pièce → … → racine
    short_m = lambda m: re.sub(r"\s*\(.*?\)", "", m)
    labels = [f"Matière actuelle\n{short_m(a['material'])}", f"Nouvelle matière\n{short_m(b['material'])}", f"Fournisseur actuel\n{a['supplier_category']}", f"Nouveau fournisseur\n{b['supplier_category']}"]
    xs, ys = [0.01] * 4, [0.12, 0.38, 0.64, 0.9]
    node_color = ["#8a97ab", C_WARN, "#8a97ab", C_BAD if ci["supplier_change"] else C_OK]
    for i, x in enumerate(chain):
        labels.append(f"{x.short}\nrév. {x.rev} → {x.next_rev}")
        node_color.append(C_BLUE if x.role_label.startswith("Pièce") else SUB_COLORS.get(x.short, "#7a5af8") if x.level == 1 else "#1c2430")
        xs.append(0.26 + 0.42 * i / max(len(chain) - 1, 1)); ys.append(0.5)
    actors = [("Bureau d'études", "revoir la géométrie" if ci["family_change"] else "valider à volume constant", True),
              ("Achats", f"consulter {b['supplier_category']}" if ci["supplier_change"] else "avenant prix/qualité", True),
              ("Qualité", "requalification pièce + PPAP", True), ("Client OEM", "demande de modification (ECR) sur " + chain[-1].short, True),
              ("Logistique", "stock de transition", ci["supplier_change"])]
    actors = [x for x in actors if x[2]]
    for j, (name, why, _) in enumerate(actors):
        labels.append(name)
        node_color.append("#0f9d8a")
        xs.append(0.99); ys.append((j + 0.5) / len(actors))
    cost_w = max(abs(float(p["est_cost"])), 1.0)
    src, dst, val, lcol = [], [], [], []
    part_idx = 4
    src += [0, 1, 2, 3]; dst += [part_idx] * 4; val += [cost_w, cost_w + abs(ci["d_cost"]), cost_w, cost_w + abs(ci["d_cost"])]
    lcol += ["rgba(138,151,171,.35)", "rgba(199,125,0,.45)", "rgba(138,151,171,.35)", "rgba(209,52,56,.4)" if ci["supplier_change"] else "rgba(26,156,91,.4)"]
    for i in range(len(chain) - 1):
        src.append(part_idx + i); dst.append(part_idx + i + 1); val.append(cost_w * 2); lcol.append("rgba(31,111,235,.35)")
    root_idx = part_idx + len(chain) - 1
    for j in range(len(actors)):
        src.append(root_idx); dst.append(root_idx + 1 + j); val.append(cost_w * 2 / len(actors)); lcol.append("rgba(15,157,138,.35)")
    fig = go.Figure(go.Sankey(arrangement="fixed", node=dict(label=[x.replace("\n", "<br>") for x in labels], x=xs, y=ys, color=node_color, pad=24, thickness=14, line=dict(color="#fff", width=1)),
                              link=dict(source=src, target=dst, value=val, color=lcol)))
    fig.update_layout(font=dict(size=11), margin=dict(l=10, r=10, t=20, b=20))
    st.plotly_chart(plotly_base(fig, 420), width="stretch", config={"displayModeBar": False})
    cols = st.columns(len(actors))
    for col, (name, why, _) in zip(cols, actors):
        kpi(col, "Acteur impacté", name, why)
    widget_close()

    l, r_ = st.columns([7, 5], gap="medium")
    with l:
        widget_open("Objets PLM impactés", "La pièce modifiée puis chaque assemblage qui la contient, jusqu'au produit racine. Révision suivante proposée.")
        imp = ci["impacted"].rename(columns={"short": "Objet", "role_label": "Type", "rev": "Rév. actuelle", "next_rev": "Rév. proposée", "maturity": "Maturité", "part_number": "Référence", "xml_line": "Ligne XML (instance)", "product_line": "Ligne XML (Product)"}).drop(columns=["occ", "level"])
        st.dataframe(imp, hide_index=True, width="stretch")
        widget_close()
    with r_:
        widget_open("Comparaison des matières", "Valeurs du référentiel d'exemple.")
        cmp = pd.DataFrame({"": ["Famille", "Prix (€/kg)", "Densité (g/cm³)", "CO₂ (kg/kg)", "Risque appro", "Facteur procédé", "Fournisseur", "Note"],
                            "Actuelle": [a["family"], a["price_eur_kg"], a["density_g_cm3"], a["co2_kg_per_kg"], a["risk_label"], a["process_factor"], a["supplier_category"], a["note"]],
                            "Nouvelle": [b["family"], b["price_eur_kg"], b["density_g_cm3"], b["co2_kg_per_kg"], b["risk_label"], b["process_factor"], b["supplier_category"], b["note"]]})
        st.dataframe(cmp.astype(str), hide_index=True, width="stretch", height=320)
        widget_close()

    ecr = (
        f"DEMANDE DE MODIFICATION (ECR) — brouillon généré\n"
        f"Objet : {p['short']} ({p['part_number']}, rév. {p['rev']}, {p['maturity']}) — XML {xml_id} L.{int(p['product_line'])}\n"
        f"Changement : matière {a['material']} → {b['material']} (volume constant)\n"
        f"Masse : {ci['d_mass']:+.3f} kg ({ci['d_mass_pct']:+.0%}) · Coût est. : {ci['d_cost']:+.1f} € ({ci['d_cost_pct']:+.0%}) · CO2 : {ci['d_co2']:+.1f} kg\n"
        f"Fournisseur : {a['supplier_category']} → {b['supplier_category']} ({'changement' if ci['supplier_change'] else 'inchangé'})\n"
        f"Risque appro : {a['risk_label']} → {b['risk_label']}\n"
        "Objets à réviser :\n" + "\n".join(f"  - {x.short} ({x.role_label}) rév. {x.rev} → {x.next_rev} — L.{x.xml_line}" for x in ci["impacted"].itertuples()) +
        "\n\nHypothèses : référentiel matières d'exemple (data/materials_ref.csv), export XPDMXML du 14/10/2026.\n"
    )
    st.download_button("Télécharger le brouillon d'ECR (.txt)", ecr, file_name=f"ECR_{p['short'].replace(' ', '_')}.txt")


# ---------------------------------------------------------------------------
# Page : Qualité des données
# ---------------------------------------------------------------------------
def page_quality():
    topbar("Qualité des données", "Ce que vaut l'export avant de décider à partir de lui")
    checks = analytics.quality_checks(MODEL)
    l, r = st.columns([7, 5], gap="medium")
    kind = {"Alerte": "bad", "À vérifier": "warn", "Info": "info", "OK": "ok"}
    with l:
        widget_open("Contrôles automatiques", "Recalculés à partir du fichier XML à chaque chargement.")
        rows = ""
        for c in checks:
            n = f"{c['count']}" + (f" / {c['total']}" if "total" in c else "")
            rows += f'<div class="plm-check"><div>{chip(c["level"], kind[c["level"]])}</div><div><div class="t">{html.escape(c["title"])}</div><div class="d">{html.escape(c["text"])}</div></div><div class="n">{n}</div></div>'
        st.markdown(rows, unsafe_allow_html=True)
        widget_close()
    with r:
        widget_open("Paramètres de l'export", "En-tête InfoHeader du paquet. Ni fichiers 3D, ni documents, ni paramètres PLM n'ont été exportés.")
        st.dataframe(MODEL.info.rename(columns={"name": "Paramètre", "value": "Valeur"}), hide_index=True, width="stretch", height=560)
        widget_close()
    widget_open("Charger un autre export", "Le même traitement s'applique à tout fichier XPDMXML (structure produit 3DEXPERIENCE), y compris un XML modifié exporté depuis cette application : le lecteur détecte l'espace de noms, la balise de masse et les préfixes de nom.")
    if st.session_state.get("load_error"):
        st.error("Le fichier chargé n'a pas pu être lu, l'export d'origine est affiché. " + st.session_state.pop("load_error"))
    up = st.file_uploader("Fichier XPDMXML", type=["xml"], label_visibility="collapsed")
    if up is not None and st.button("Analyser ce fichier"):
        st.session_state.xml_upload = up.getvalue()
        st.session_state.additions, st.session_state.edits, st.session_state.chat = [], {}, []
        load_all.clear()
        st.rerun()
    if st.session_state.xml_upload and st.button("Revenir au fichier d'origine"):
        st.session_state.xml_upload = None
        load_all.clear()
        st.rerun()
    widget_close()


# ---------------------------------------------------------------------------
# Page : Assistant
# ---------------------------------------------------------------------------
SUGGESTIONS = [
    "Quels sont les sous-ensembles du moteur et leur masse ?",
    "Quels composants sont critiques dans le réducteur ?",
    "Quel est l'impact si le carter gauche passe en magnésium ?",
    "Que se passe-t-il avec +30 % sur l'aluminium ?",
    "Quelles sont les anomalies de l'export ?",
    "Où sont utilisés les roulements ?",
]


def page_assistant():
    topbar("Assistant PLM", "Interrogez la nomenclature en langage naturel ; chaque réponse cite les objets XML consultés")
    tb = assistant.Toolbox(MODEL, MATS, RISK)
    online = LLM["kind"] != "offline"
    if LLM["kind"] == "claude":
        st.caption(f"Mode Claude ({assistant.MODEL_ID}) : le modèle choisit les outils à appeler, les résultats portent leurs références XML.")
    elif LLM["kind"] == "openai":
        st.caption(f"Modèle génératif : {LLM['label']}. Il appelle les mêmes outils ; si le modèle ne gère pas les outils, il rédige à partir des données extraites par le routeur.")
    else:
        st.info("Aucun modèle génératif configuré : l'assistant fonctionne en **mode hors ligne** (routeur par intention vers les mêmes outils, réponses sourcées).", icon="ℹ️")
        with st.expander("Brancher un modèle d'IA générative sans clé Anthropic"):
            st.markdown(
                "**Option 1 — Ollama en local (gratuit, recommandé sur Mac)** : installez [ollama.com](https://ollama.com), puis dans un terminal :\n"
                "```bash\nollama pull qwen2.5:7b\n```\n"
                "Relancez l'application : Ollama est détecté automatiquement (port 11434). Pour choisir un autre modèle :\n"
                "```bash\nexport LLM_BASE_URL=http://localhost:11434/v1\nexport LLM_MODEL=llama3.1:8b\n```\n"
                "**Option 2 — LM Studio** (modèles Hugging Face avec interface graphique) : démarrez son serveur local, puis `LLM_BASE_URL=http://localhost:1234/v1` et `LLM_MODEL=<nom du modèle chargé>`.\n\n"
                "**Option 3 — API gratuites en ligne** (compatibles OpenAI) : Groq (`https://api.groq.com/openai/v1`, modèle `llama-3.3-70b-versatile`), Mistral (`https://api.mistral.ai/v1`, `mistral-small-latest`), "
                "OpenRouter (`https://openrouter.ai/api/v1`, modèles gratuits suffixés `:free`). Définissez `LLM_BASE_URL`, `LLM_MODEL` et `LLM_API_KEY`.\n\n"
                "Dans tous les cas, le modèle n'a accès aux données que par les outils de l'application, et chaque réponse cite les objets XML consultés.")

    cols = st.columns(3)
    for i, s in enumerate(SUGGESTIONS):
        if cols[i % 3].button(s, key=f"sug{i}", width="stretch"):
            st.session_state["pending_q"] = s

    for msg in st.session_state.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for call in msg.get("calls", []):
                with st.expander(f"Outil `{call['name']}` — {len(call['refs'])} source(s)"):
                    st.json(call["args"])
                    st.markdown('<div class="plm-refs">' + " · ".join(html.escape(x) for x in call["refs"][:20]) + "</div>", unsafe_allow_html=True)
                    for ref in call["refs"][:3]:
                        if "L." in ref:
                            try:
                                st.code(xml_snippet(XML_LINES, int(ref.split("L.")[-1]), 12), language="xml")
                            except ValueError:
                                pass

    q = st.chat_input("Posez une question sur le produit…") or st.session_state.pop("pending_q", None)
    if q:
        st.session_state.chat.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Consultation des données PLM…"):
                try:
                    if LLM["kind"] == "claude":
                        text, calls, hist = assistant.ask_claude(tb, st.session_state.get("claude_history", []), q)
                        st.session_state.claude_history = hist
                    elif LLM["kind"] == "openai":
                        text, calls, hist = assistant.ask_openai_compatible(tb, LLM, st.session_state.get("llm_history", [])[-8:], q)
                        st.session_state.llm_history = hist
                    else:
                        text, calls = assistant.ask_offline(tb, q)
                except Exception as e:  # clé invalide, réseau…
                    text, calls = f"Erreur lors de l'appel au modèle : {e}\n\nRéponse en mode hors ligne :\n\n" + assistant.ask_offline(tb, q)[0], []
            st.markdown(text)
        st.session_state.chat.append({"role": "assistant", "content": text, "calls": [{"name": c.name, "args": c.args, "refs": c.refs} for c in calls]})
        st.rerun()
    if st.session_state.chat and st.button("Effacer la conversation"):
        st.session_state.chat, st.session_state.claude_history, st.session_state.llm_history = [], [], []
        st.rerun()



# ---------------------------------------------------------------------------
# Page : Fournisseurs (catalogue, contrats, performance)
# ---------------------------------------------------------------------------
def stars(n: int) -> str:
    return "★" * int(n) + "☆" * (5 - int(n))


def page_suppliers():
    topbar("Fournisseurs", "Catalogue, contrats et performance du réseau de fournisseurs (données du fournisseur, pas de l'OEM)")
    prim = SUPPLIERS.loc[SUPPLIERS["primary"]]
    exp = prim.loc[prim["contract_status"].isin(["Expiré", "Expire < 3 mois", "Expire < 6 mois"])]
    c = st.columns(5)
    kpi(c[0], "Fournisseurs au catalogue", len(SUPPLIERS), f"{len(prim)} principaux · {len(SUPPLIERS) - len(prim)} alternatifs")
    kpi(c[1], "Contrats à renouveler", len(exp), "échéance sous 6 mois", sample=len(exp) > 0)
    kpi(c[2], "Indice ESG moyen", f"{prim['esg_index'].mean():.0f}", "fournisseurs principaux, sur 100")
    kpi(c[3], "Délai de réponse moyen", f"{prim['answer_time_days'].mean():.1f}", "jours (RFQ)", unit=" j")
    kpi(c[4], "Catégories sans seconde source", int((SUPPLIERS.groupby("supplier_category")["supplier_id"].count() == 1).sum()), f"sur {SUPPLIERS['supplier_category'].nunique()} catégories", sample=True)
    st.write("")

    left, right = st.columns([3, 9], gap="medium")
    with left:
        widget_open("Catalogue")
        names = SUPPLIERS["name"].tolist()
        sel = st.radio("Fournisseur", names, index=names.index(st.session_state.get("sel_supplier", names[0])) if st.session_state.get("sel_supplier") in names else 0,
                       format_func=lambda n: f"{n} · {stars(SUPPLIERS.set_index('name').loc[n, 'rating'])}", label_visibility="collapsed")
        st.session_state.sel_supplier = sel
        widget_close()
    with right:
        s = SUPPLIERS.set_index("name").loc[sel]
        parts_s = RISK.loc[RISK["supplier_category"] == s["supplier_category"]] if s["primary"] else RISK.iloc[0:0]
        widget_open(f"{sel}", f"{s['supplier_category']} · {s['city']}, {s['country']} · {s['note']}")
        a, b, d, e = st.columns(4)
        kpi(a, "Note", stars(s["rating"]), f"{s['rating']}/5")
        kpi(b, "Indice ESG", int(s["esg_index"]), "sur 100", sample=s["esg_index"] < st.session_state.weights["esg_floor"])
        kpi(d, "Délai de réponse", int(s["answer_time_days"]), "jours", unit=" j")
        st_chip = {"En cours": "ok", "Expire < 6 mois": "warn", "Expire < 3 mois": "bad", "Expiré": "bad", "Sans contrat": "info"}[s["contract_status"]]
        kpi(e, "Contrat", chip(s["contract_status"], st_chip), f"{s['contract_type']}" + (f" · fin {s['contract_end']:%d/%m/%Y}" if pd.notna(s["contract_end"]) else ""))
        widget_close()

        tabs = st.tabs(["Pièces nominées", "Contrat", "Performance du réseau", "Sites"])
        with tabs[0]:
            if parts_s.empty:
                st.info("Fournisseur alternatif : aucune pièce ne lui est attribuée aujourd'hui. Voir « Risques & couverture » pour le proposer en seconde source.")
            else:
                v = parts_s[["short", "part_number", "rev", "material", "qty", "est_cost", "cost_source", "score", "tier"]].copy()
                v["Capacité"] = s["capacity_pct"] / 100
                v = v.rename(columns={"short": "Pièce", "part_number": "Référence", "rev": "Rév.", "material": "Matière", "qty": "Qté", "est_cost": "Coût (€)", "cost_source": "Source du coût", "score": "Criticité", "tier": "Niveau"})
                st.dataframe(v, hide_index=True, width="stretch", column_config={"Criticité": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"), "Capacité": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"), "Coût (€)": st.column_config.NumberColumn(format="%.0f")})
                st.caption(f"{len(v)} références · {fmt_eur(v['Coût (€)'].sum())} estimés · {v['Qté'].sum()} pièces par groupe motopropulseur.")
        with tabs[1]:
            if pd.isna(s["contract_end"]):
                st.info("Pas de contrat en cours avec ce fournisseur.")
            else:
                st.markdown(f"**{s['contract_type']}** du {s['contract_start']:%d/%m/%Y} au {s['contract_end']:%d/%m/%Y} · {s['months_left']:.1f} mois restants · capacité allouée {s['capacity_pct']} %")
                if s["months_left"] <= st.session_state.weights["contract_months"]:
                    st.warning(f"Échéance sous {st.session_state.weights['contract_months']} mois : les pièces de ce fournisseur reçoivent +{st.session_state.weights['contract_points']} points de criticité. Renégocier ou consulter.")
            tl = extras.contract_timeline(SUPPLIERS)
            fig = px.timeline(tl, x_start="start", x_end="contract_end", y="name", color="contract_status",
                              color_discrete_map={"En cours": C_OK, "Expire < 6 mois": C_WARN, "Expire < 3 mois": C_BAD, "Expiré": "#6b7280"},
                              hover_data={"contract_type": True, "months_left": True, "start": False, "contract_end": "|%d/%m/%Y"})
            fig.add_vline(x=pd.Timestamp(extras.TODAY).timestamp() * 1000, line_dash="dash", line_color="#1c2430")
            fig.update_yaxes(autorange="reversed", title=None)
            fig.update_xaxes(title=None)
            fig.update_layout(legend_title=None)
            st.plotly_chart(plotly_base(fig, 360), width="stretch", config={"displayModeBar": False})
            st.caption("Ligne pointillée : aujourd'hui. Les durées de contrat viennent du catalogue fournisseur (data/suppliers.csv).")
        with tabs[2]:
            a, b = st.columns(2)
            with a:
                e = prim.sort_values("esg_index")
                fig = px.bar(e, x="name", y="esg_index", text="esg_index", color="esg_index", color_continuous_scale=["#bfe3b4", "#2f9e44"])
                fig.update_traces(textposition="outside", marker_line_width=0)
                fig.update_layout(margin=dict(t=40), title="Indice ESG des fournisseurs principaux", xaxis_title=None, yaxis_title=None, coloraxis_showscale=False, title_font_size=13)
                room(fig, e["esg_index"], "y", 1.15)
                st.plotly_chart(plotly_base(fig, 320), width="stretch", config={"displayModeBar": False})
            with b:
                t = prim.sort_values("answer_time_days")
                fig = px.bar(t, x="name", y="answer_time_days", text="answer_time_days", color="answer_time_days", color_continuous_scale=["#9ec5fe", "#0b3d91"])
                fig.update_traces(textposition="outside", marker_line_width=0)
                fig.update_layout(margin=dict(t=40), title="Délai de réponse aux RFQ (jours)", xaxis_title=None, yaxis_title=None, coloraxis_showscale=False, title_font_size=13)
                room(fig, t["answer_time_days"], "y", 1.15)
                st.plotly_chart(plotly_base(fig, 320), width="stretch", config={"displayModeBar": False})
            cost_by_sup = RISK.groupby("supplier")["est_cost"].sum().reindex(prim["name"]).fillna(0)
            sc = prim.assign(cost=cost_by_sup.values)
            fig = px.scatter(sc, x="esg_index", y="answer_time_days", size="cost", color="contract_status", text="name", size_max=42,
                             color_discrete_map={"En cours": C_OK, "Expire < 6 mois": C_WARN, "Expire < 3 mois": C_BAD, "Expiré": "#6b7280"},
                             labels={"esg_index": "Indice ESG", "answer_time_days": "Délai de réponse (j)"})
            fig.update_traces(textposition="top center", textfont_size=10)
            fig.update_layout(margin=dict(t=40), title="Positionnement : ESG × réactivité, taille = coût confié", legend_title=None, title_font_size=13)
            st.plotly_chart(plotly_base(fig, 380), width="stretch", config={"displayModeBar": False})
        with tabs[3]:
            st.markdown("**Carte ESG du réseau** : couleur = indice ESG (rouge faible → vert élevé), taille = coût confié (les alternatifs, sans pièce attribuée, sont les petits points). Zoom, déplacement et survol pour le détail.")
            f1, f2, f3 = st.columns([2, 2, 3])
            esg_min = f1.slider("Indice ESG minimum", 0, 100, 0, 5)
            show_alt = f2.toggle("Afficher les alternatifs", value=True)
            cats = f3.multiselect("Catégories", sorted(SUPPLIERS["supplier_category"].unique()), default=[], placeholder="Toutes les catégories")
            m = SUPPLIERS.copy()
            m["cost"] = m["name"].map(RISK.groupby("supplier")["est_cost"].sum()).fillna(0)
            m["size"] = m["cost"] + 8
            m = m.loc[(m["esg_index"] >= esg_min) & (m["primary"] | show_alt)]
            if cats:
                m = m.loc[m["supplier_category"].isin(cats)]
            m["Statut"] = m["primary"].map({True: "Principal", False: "Alternatif"})
            fig = px.scatter_map(m, lat="lat", lon="lon", color="esg_index", size="size", size_max=26, zoom=4.1, center=dict(lat=47.8, lon=8.5), hover_name="name",
                                 color_continuous_scale=["#d13438", "#f2c14e", "#1a9c5b"], range_color=[40, 90], opacity=0.9,
                                 hover_data={"supplier_category": True, "city": True, "country": True, "esg_index": True, "rating": True, "answer_time_days": True, "contract_status": True, "Statut": True, "cost": ":.0f", "lat": False, "lon": False, "size": False},
                                 labels={"esg_index": "ESG", "supplier_category": "Catégorie", "city": "Ville", "country": "Pays", "rating": "Note", "answer_time_days": "Délai (j)", "contract_status": "Contrat", "cost": "Coût confié (€)"},
                                 map_style="open-street-map")
            fig.update_layout(coloraxis_colorbar=dict(title="ESG", thickness=12, len=0.6), legend=dict(orientation="h", y=1.02, x=0))
            st.plotly_chart(plotly_base(fig, 520), width="stretch", config={"scrollZoom": True})
            st.caption("Fond de carte OpenStreetMap (connexion internet nécessaire). Indices ESG : données d'exemple du catalogue.")
            lo = m.sort_values("esg_index").head(3)
            if not lo.empty:
                st.markdown("**Points d'attention ESG** : " + " · ".join(f"{x.name} ({x.esg_index}, {x.supplier_category})" for x in lo.itertuples()))
            st.dataframe(m[["name", "supplier_category", "city", "country", "esg_index", "rating", "Statut", "contract_status"]].rename(columns={"name": "Fournisseur", "supplier_category": "Catégorie", "city": "Ville", "country": "Pays", "esg_index": "ESG", "rating": "Note", "contract_status": "Contrat"}).sort_values("ESG"), hide_index=True, width="stretch", height=240)


# ---------------------------------------------------------------------------
# Page : Risques & couverture
# ---------------------------------------------------------------------------
RISK_TYPES = ["Composant", "Fournisseur", "Externe", "Matière", "Qualité", "Logistique", "Réglementaire"]


def all_risks() -> list[dict]:
    return extras.auto_risks(RISK, SUPPLIERS, MATS) + st.session_state.risks_manual


def page_risks():
    topbar("Risques & couverture", "Registre des risques (automatiques et saisis), matrice probabilité × impact, options de couverture")
    risks = all_risks()
    open_r = [r for r in risks if r["status"] != "Clos"]
    c = st.columns(4)
    kpi(c[0], "Risques ouverts", len(open_r), f"{sum(1 for r in open_r if r['source'] == 'auto')} déduits des données · {sum(1 for r in open_r if r['source'] != 'auto')} saisis")
    kpi(c[1], "Risques majeurs", sum(1 for r in open_r if r["probability"] * r["impact"] >= 12), "probabilité × impact ≥ 12", sample=True)
    kpi(c[2], "Contrats à sécuriser", sum(1 for r in open_r if r["type"] == "Fournisseur"), "échéance sous 6 mois")
    kpi(c[3], "Exposition matières volatiles", fmt_eur(float((RISK.loc[RISK["family"].isin(["Aluminium", "Cuivre"]), "est_cost"] / RISK.loc[RISK["family"].isin(["Aluminium", "Cuivre"]), "process_factor"]).sum())), "part matière alu + cuivre par GMP", sample=True)
    st.write("")

    l, r_ = st.columns([5, 7], gap="medium")
    with l:
        widget_open("Matrice des risques", "Nombre de risques ouverts par probabilité (lignes) et impact (colonnes).")
        mtx = extras.risk_matrix(risks)
        sev = np.add.outer(np.arange(1, 6), np.zeros(5)) * np.add.outer(np.zeros(5), np.arange(1, 6))
        colors = [[0, "#8fd19e"], [0.3, "#8fd19e"], [0.3, "#ffe066"], [0.55, "#ffe066"], [0.55, "#e03131"], [1, "#e03131"]]
        fig = go.Figure(go.Heatmap(z=sev / 25, x=["1 · Négligeable", "2 · Mineur", "3 · Modéré", "4 · Majeur", "5 · Élevé"], y=["1 · Rare", "2 · Improbable", "3 · Possible", "4 · Probable", "5 · Quasi certain"],
                                   colorscale=colors, showscale=False, xgap=3, ygap=3, hoverinfo="skip"))
        for i in range(5):
            for j in range(5):
                if mtx[i, j]:
                    fig.add_annotation(x=j, y=i, text=f"<b>{mtx[i, j]}</b>", showarrow=False, font=dict(size=16, color="#1c2430"))
        fig.update_layout(xaxis_title="Impact", yaxis_title="Probabilité")
        st.plotly_chart(plotly_base(fig, 360), width="stretch", config={"displayModeBar": False})
        widget_close()

        widget_open("Ajouter un risque", "Risque externe au produit, événement fournisseur, aléa réglementaire… Il entre dans la matrice et le registre.")
        with st.form("add_risk", clear_on_submit=True):
            title = st.text_input("Intitulé", placeholder="Ex. : droits de douane sur les tôles magnétiques importées")
            a, b = st.columns(2)
            rtype = a.selectbox("Type", RISK_TYPES, index=2)
            linked = b.selectbox("Lié à", ["—"] + sorted(RISK["short"].unique().tolist()) + sorted(SUPPLIERS["name"].tolist()) + sorted(MATS["family"].unique().tolist()))
            a, b = st.columns(2)
            prob = a.slider("Probabilité", 1, 5, 3)
            imp = b.slider("Impact", 1, 5, 3)
            mitig = st.text_input("Mesure de couverture envisagée", placeholder="Ex. : seconde source, contrat à terme, stock de sécurité")
            if st.form_submit_button("Ajouter au registre", type="primary") and title:
                fam = linked if linked in set(MATS["family"]) else (RISK.set_index("short")["family"].get(linked, "") if linked in set(RISK["short"]) else "")
                cat = SUPPLIERS.set_index("name")["supplier_category"].get(linked, "") if linked in set(SUPPLIERS["name"]) else (RISK.set_index("short")["supplier_category"].get(linked, "") if linked in set(RISK["short"]) else "")
                st.session_state.risks_manual.append(dict(id=f"M-{len(st.session_state.risks_manual) + 1}", title=title, type=rtype, source="manuel", probability=prob, impact=imp,
                                                          linked=linked if linked != "—" else "", linked_id="", family=fam, supplier_category=cat, mitigation=mitig, status="Ouvert"))
                st.rerun()
        widget_close()

    with r_:
        widget_open("Registre des risques", "Cliquez sur une ligne pour voir les options de couverture. Les risques « auto » sont recalculés à chaque chargement à partir de la criticité, des contrats et des marchés.")
        df = pd.DataFrame(risks)
        df["Sévérité"] = df["probability"] * df["impact"]
        df = df.sort_values("Sévérité", ascending=False)
        view = df[["title", "type", "source", "probability", "impact", "Sévérité", "mitigation", "status", "id"]].rename(columns={"title": "Risque", "type": "Type", "source": "Source", "probability": "Prob.", "impact": "Impact", "mitigation": "Couverture", "status": "Statut"})
        ev = st.dataframe(view, hide_index=True, width="stretch", height=380, on_select="rerun", selection_mode="single-row", key="risk_reg",
                          column_config={"id": None, "Sévérité": st.column_config.ProgressColumn(min_value=0, max_value=25, format="%d"), "Risque": st.column_config.TextColumn(width="large")})
        picked = df.iloc[ev.selection.rows[0]] if ev.selection.rows else df.iloc[0]
        if picked["source"] != "auto":
            a, b = st.columns(2)
            if a.button("Clore ce risque" if picked["status"] != "Clos" else "Rouvrir ce risque"):
                for m in st.session_state.risks_manual:
                    if m["id"] == picked["id"]:
                        m["status"] = "Clos" if m["status"] != "Clos" else "Ouvert"
                st.rerun()
            if b.button("Supprimer"):
                st.session_state.risks_manual = [m for m in st.session_state.risks_manual if m["id"] != picked["id"]]
                st.rerun()
        st.download_button("Exporter le registre (CSV)", df.drop(columns=["linked_id"]).to_csv(index=False).encode("utf-8"), "registre_risques.csv")
        widget_close()

        widget_open(f"Couverture : {picked['title']}", "Alternatives classées par pertinence : couverture financière (forward, futures), matière de substitution, fournisseur alternatif, contrat, stock.")
        opts = extras.hedging_options(RISK, MATS, SUPPLIERS, family=picked.get("family", ""), supplier_category=picked.get("supplier_category", ""), xml_id=picked.get("linked_id", ""))
        if not opts:
            st.info("Pas de proposition automatique pour ce risque : renseignez la mesure de couverture dans le registre.")
        kind_chip = {"Couverture financière": "info", "Matière alternative": "ok", "Fournisseur alternatif": "warn", "Contrat": "fill", "Stock": "fill"}
        for o in opts:
            st.markdown(f"{chip(o['kind'], kind_chip.get(o['kind'], 'info'))} &nbsp;**{html.escape(o['label'])}**<br><span style='color:#5d6b7e;font-size:.84rem'>{html.escape(o['detail'])}</span>", unsafe_allow_html=True)
        widget_close()


# ---------------------------------------------------------------------------
# Page : Mise à jour de la nomenclature
# ---------------------------------------------------------------------------
def page_bom_update():
    topbar("Mise à jour de la nomenclature", "Ajoutez un composant, un sous-assemblage ou un lot de composants : ils entrent dans toutes les analyses")
    st.markdown('<p class="plm-lede">L\'export OEM reste intact : les ajouts sont superposés à la nomenclature, marqués « Ajout manuel », et pris en compte dans les masses, la criticité, les fournisseurs et l\'assistant. Un prix saisi remplace l\'estimation matière.</p>', unsafe_allow_html=True)
    l, r_ = st.columns([5, 7], gap="medium")
    asm = MODEL.bom.loc[MODEL.bom["role"] == "assembly"].sort_values("level")
    asm_labels = {o: f"{'   ' * int(lv)}{s} (rév. {rv})" for o, s, lv, rv in zip(asm["occ"], asm["short"], asm["level"], asm["rev"])}
    with l:
        widget_open("Ajouter un composant ou un sous-assemblage")
        with st.form("add_comp", clear_on_submit=True):
            kind = st.radio("Type", ["Pièce", "Sous-assemblage"], horizontal=True)
            name = st.text_input("Nom", placeholder="Ex. : Capteur de position rotor")
            parent = st.selectbox("Assemblage parent", list(asm_labels), format_func=asm_labels.get)
            a, b = st.columns(2)
            material = a.selectbox("Matière", ["—"] + MATS["material"].tolist())
            rev = b.selectbox("Révision", list("ABCD"))
            a, b, d = st.columns(3)
            mass = a.number_input("Masse unitaire (kg)", min_value=0.0, value=0.1, step=0.01, format="%.3f")
            qty = b.number_input("Quantité", min_value=1, value=1, step=1)
            price = d.number_input("Prix unitaire (€, optionnel)", min_value=0.0, value=0.0, step=0.5)
            desc = st.text_input("Description", placeholder="Optionnel")
            if st.form_submit_button("Ajouter à la nomenclature", type="primary") and name:
                st.session_state.additions.append(dict(name=name, kind="part" if kind == "Pièce" else "assembly", parent_occ=parent, material="" if material == "—" else material,
                                                       mass_kg=mass, qty=int(qty), rev=rev, unit_cost=price or None, description=desc))
                st.rerun()
        widget_close()
        widget_open("Importer un lot (CSV)", "Colonnes : name, kind (part|assembly), parent_occ, material, mass_kg, qty, rev, unit_cost, description. Les occ des assemblages sont listées à droite.")
        up = st.file_uploader("Fichier CSV", type=["csv"], label_visibility="collapsed")
        if up is not None and st.button("Importer"):
            df = pd.read_csv(up).fillna("")
            n = 0
            for _, x in df.iterrows():
                if x.get("name") and str(x.get("parent_occ")) in asm_labels:
                    st.session_state.additions.append(dict(name=x["name"], kind=x.get("kind", "part") or "part", parent_occ=str(x["parent_occ"]), material=x.get("material", ""), mass_kg=float(x.get("mass_kg") or 0),
                                                           qty=int(x.get("qty") or 1), rev=x.get("rev", "A") or "A", unit_cost=float(x["unit_cost"]) if x.get("unit_cost") not in ("", None) else None, description=x.get("description", "")))
                    n += 1
            st.success(f"{n} ligne(s) importée(s).")
            st.rerun()
        st.download_button("Modèle CSV", "name,kind,parent_occ,material,mass_kg,qty,rev,unit_cost,description\nCapteur de position rotor,part,79,Polyamide PA66-GF30,0.08,2,A,18.5,Capteur à effet Hall\nModule onduleur,assembly,0,,0,1,A,,Électronique de puissance\n", "modele_ajouts.csv")
        widget_close()
        widget_open("Modifier un composant existant", "Révision, maturité, matière, masse, prix ou description. La cohérence parent/enfant est vérifiée : un parent ne peut pas être plus mûr que ses enfants.")
        comp = MODEL.bom.loc[MODEL.bom["role"] != "geometry"].drop_duplicates("xml_id")
        comp_labels = {x.xml_id: f"{x.short} · rév. {x.rev} · {x.maturity}" for x in comp.itertuples()}
        with st.form("edit_comp"):
            target = st.selectbox("Composant", list(comp_labels), format_func=comp_labels.get, index=list(comp_labels).index(st.session_state.get("edit_target", "ID_54")) if st.session_state.get("edit_target", "ID_54") in comp_labels else 0)
            cur = comp.set_index("xml_id").loc[target]
            a, b = st.columns(2)
            e_rev = a.selectbox("Révision", list("ABCDEF"), index="ABCDEF".index(cur["rev"][0]) if cur["rev"][:1] in "ABCDEF" else 0)
            e_mat = b.selectbox("Maturité", ["IN_WORK", "FROZEN", "RELEASED"], index=["IN_WORK", "FROZEN", "RELEASED"].index(cur["maturity"]) if cur["maturity"] in ("IN_WORK", "FROZEN", "RELEASED") else 0)
            e_material = st.selectbox("Matière", ["(inchangée)"] + MATS["material"].tolist()) if cur["role"] == "part" else "(inchangée)"
            a, b = st.columns(2)
            e_mass = a.number_input("Masse unitaire (kg)", min_value=0.0, value=float(cur["mass_kg"]), step=0.01, format="%.3f") if cur["role"] == "part" else float(cur["mass_kg"])
            e_cost = b.number_input("Prix unitaire (€, 0 = estimation)", min_value=0.0, value=float(cur["cost_override"]) if pd.notna(cur.get("cost_override")) else 0.0, step=0.5)
            e_desc = st.text_input("Description", value=cur["description"] or "")
            if st.form_submit_button("Enregistrer la modification", type="primary"):
                e = {}
                if e_rev != cur["rev"]: e["rev"] = e_rev
                if e_mat != cur["maturity"]: e["maturity"] = e_mat
                if e_material != "(inchangée)" and e_material != cur["material"]: e["material"] = e_material
                if cur["role"] == "part" and abs(e_mass - float(cur["mass_kg"])) > 1e-9: e["mass_kg"] = e_mass
                if e_cost: e["unit_cost"] = e_cost
                if e_desc != (cur["description"] or ""): e["description"] = e_desc
                if e:
                    st.session_state.edits[target] = {**st.session_state.edits.get(target, {}), **e}
                    # contrôle de cohérence immédiat
                    trial = extras.apply_additions(BASE_MODEL, st.session_state.additions, st.session_state.edits)
                    v = analytics.maturity_consistency(trial)
                    if "maturity" in e and ((v["parent_occ"].isin(trial.bom.loc[trial.bom["xml_id"] == target, "occ"])).any() or (v["child_occ"].isin(trial.bom.loc[trial.bom["xml_id"] == target, "occ"])).any()):
                        st.session_state["edit_warning"] = f"Modification enregistrée, mais elle crée une incohérence de maturité ({len(v)} lien(s) parent/enfant). Voir « Qualité des données »."
                    st.rerun()
        if st.session_state.get("edit_warning"):
            st.warning(st.session_state.pop("edit_warning"))
        widget_close()
    with r_:
        widget_open("Ajouts en cours", f"{len(st.session_state.additions)} ajout(s). Supprimez une ligne pour revenir en arrière ; l'export OEM n'est jamais modifié.")
        if st.session_state.additions:
            df = pd.DataFrame(st.session_state.additions)
            df["parent"] = df["parent_occ"].map(lambda o: asm_labels.get(o, o).strip())
            view = df[["name", "kind", "parent", "material", "mass_kg", "qty", "rev", "unit_cost"]].rename(columns={"name": "Nom", "kind": "Type", "parent": "Parent", "material": "Matière", "mass_kg": "Masse (kg)", "qty": "Qté", "rev": "Rév.", "unit_cost": "Prix (€)"})
            ev = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="add_tbl")
            a, b = st.columns(2)
            if ev.selection.rows and a.button("Supprimer la ligne sélectionnée"):
                st.session_state.additions.pop(ev.selection.rows[0])
                st.rerun()
            if b.button("Tout annuler"):
                st.session_state.additions = []
                st.rerun()
        else:
            st.info("Aucun ajout pour l'instant. Le formulaire de gauche ajoute un composant ; le CSV en ajoute plusieurs.")
        widget_close()
        widget_open("Modifications en cours", f"{len(st.session_state.edits)} composant(s) modifié(s).")
        if st.session_state.edits:
            names = MODEL.bom.drop_duplicates("xml_id").set_index("xml_id")["short"]
            ed = pd.DataFrame([{"Composant": names.get(k, k), "Champs modifiés": ", ".join(f"{a} = {b}" for a, b in v.items()), "xml_id": k} for k, v in st.session_state.edits.items()])
            ev2 = st.dataframe(ed, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="edit_tbl", column_config={"xml_id": None})
            a, b = st.columns(2)
            if ev2.selection.rows and a.button("Annuler cette modification"):
                st.session_state.edits.pop(ed.iloc[ev2.selection.rows[0]]["xml_id"], None)
                st.rerun()
            if b.button("Annuler toutes les modifications"):
                st.session_state.edits = {}
                st.rerun()
        else:
            st.info("Aucune modification. Utilisez « Modifier un composant existant » à gauche.")
        widget_close()
        widget_open("Exporter", "Le XML modifié garde le schéma XPDMXML de l'OEM : objets existants mis à jour, nouveaux objets Product + ProductInst, en-tête tracé (Info ModifiedBy). Il peut être rechargé dans cette application ou transmis.")
        src = ROOT / "data" / "_uploaded.xml" if st.session_state.xml_upload else XML_PATH
        try:
            xml_bytes = extras.export_xml(src, MODEL, st.session_state.additions, st.session_state.edits)
            st.download_button("Télécharger le XML modifié (XPDMXML)", xml_bytes, f"XPDMXML_modifie_{extras.TODAY.isoformat()}.xml", type="primary", width="stretch")
        except Exception as e:
            st.error(f"Export impossible : {e}")
        out = MODEL.bom.drop(columns=[c for c in ("children",) if c in MODEL.bom.columns])
        st.download_button("Nomenclature enrichie (CSV)", out.to_csv(index=False).encode("utf-8"), "nomenclature_enrichie.csv", width="stretch")
        widget_close()
        widget_open("Nomenclature enrichie", "Vue de contrôle : masses recalculées, ajouts surlignés par leur origine.")
        v = MODEL.bom.loc[MODEL.bom["role"] != "geometry", ["short", "level", "role_label", "rev", "material", "mass_kg", "origin", "occ"]].copy()
        v["short"] = v.apply(lambda x: (" " * int(x["level"])) + x["short"], axis=1)
        v = v.rename(columns={"short": "Composant", "role_label": "Type", "rev": "Rév.", "material": "Matière", "mass_kg": "Masse (kg)", "origin": "Origine", "occ": "occ"}).drop(columns="level")
        st.dataframe(v, hide_index=True, width="stretch", height=380, column_config={"Masse (kg)": st.column_config.NumberColumn(format="%.3f")})
        widget_close()


# ---------------------------------------------------------------------------
# Page : Paramètres de criticité
# ---------------------------------------------------------------------------
def page_settings():
    topbar("Paramètres de criticité", "Adaptez les critères, leurs pondérations et les seuils aux besoins du client ; la table se recalcule en direct")
    w = st.session_state.weights
    l, r_ = st.columns([5, 7], gap="medium")
    with l:
        widget_open("Critères de base", "Parts relatives ; elles sont normalisées pour donner un score de base sur 100.")
        for k in ("w_risk", "w_cost", "w_mass", "w_qty"):
            w[k] = st.slider(analytics.WEIGHT_LABELS[k], 0, 100, int(w[k]), 5, key=f"s_{k}")
        tot = w["w_risk"] + w["w_cost"] + w["w_mass"] + w["w_qty"]
        st.caption(f"Total {tot} → parts normalisées : risque {w['w_risk'] / max(tot, 1):.0%}, coût {w['w_cost'] / max(tot, 1):.0%}, masse {w['w_mass'] / max(tot, 1):.0%}, occurrences {w['w_qty'] / max(tot, 1):.0%}.")
        widget_close()
        widget_open("Malus additionnels", "Points ajoutés au score de base (plafonné à 100).")
        a, b = st.columns(2)
        w["rev_points"] = a.slider(analytics.WEIGHT_LABELS["rev_points"], 0, 20, int(w["rev_points"]), key="s_rev_points")
        w["rev_cap"] = b.slider(analytics.WEIGHT_LABELS["rev_cap"], 0, 40, int(w["rev_cap"]), key="s_rev_cap")
        a, b = st.columns(2)
        w["contract_points"] = a.slider(analytics.WEIGHT_LABELS["contract_points"], 0, 30, int(w["contract_points"]), key="s_cp")
        w["contract_months"] = b.slider(analytics.WEIGHT_LABELS["contract_months"], 1, 24, int(w["contract_months"]), key="s_cm")
        a, b = st.columns(2)
        w["esg_points"] = a.slider(analytics.WEIGHT_LABELS["esg_points"], 0, 30, int(w["esg_points"]), key="s_ep")
        w["esg_floor"] = b.slider(analytics.WEIGHT_LABELS["esg_floor"], 0, 100, int(w["esg_floor"]), 5, key="s_ef")
        widget_close()
        widget_open("Seuils des niveaux")
        a, b = st.columns(2)
        w["crit"] = a.slider(analytics.WEIGHT_LABELS["crit"], 10, 100, int(w["crit"]), 5, key="s_crit")
        w["watch"] = b.slider(analytics.WEIGHT_LABELS["watch"], 0, int(w["crit"]) - 5, min(int(w["watch"]), int(w["crit"]) - 5), 5, key="s_watch")
        a, b = st.columns(2)
        if a.button("Rétablir les valeurs par défaut"):
            st.session_state.weights = dict(analytics.DEFAULT_WEIGHTS)
            for k in list(st.session_state):
                if k.startswith("s_"):
                    del st.session_state[k]
            st.rerun()
        b.download_button("Exporter le profil (JSON)", json.dumps(w, ensure_ascii=False, indent=2), "profil_criticite.json")
        widget_close()
    with r_:
        new = analytics.part_risk(MODEL, MATS, w, SUPPLIERS)
        base = analytics.part_risk(MODEL, MATS, analytics.DEFAULT_WEIGHTS, SUPPLIERS).set_index("xml_id")
        default = base["score"]
        changed = int((new["tier"].values != new["xml_id"].map(base["tier"]).values).sum())
        c = st.columns(3)
        kpi(c[0], "Critiques", int((new["tier"] == "Critique").sum()), f"seuil ≥ {w['crit']}")
        kpi(c[1], "À surveiller", int((new["tier"] == "À surveiller").sum()), f"seuil ≥ {w['watch']}")
        kpi(c[2], "Changements de niveau", changed, "par rapport au profil par défaut")
        widget_open("Aperçu du classement avec ce profil", "Δ : écart de score par rapport au profil par défaut.")
        v = new[["short", "rev", "material", "supplier", "contract_status", "est_cost", "score_base", "score", "tier", "xml_id"]].copy()
        v["Δ"] = v["score"] - v["xml_id"].map(default)
        v = v.drop(columns="xml_id").rename(columns={"short": "Composant", "rev": "Rév.", "material": "Matière", "supplier": "Fournisseur", "contract_status": "Contrat", "est_cost": "Coût (€)", "score_base": "Base", "score": "Score", "tier": "Niveau"})
        st.dataframe(v, hide_index=True, width="stretch", height=560, column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"), "Coût (€)": st.column_config.NumberColumn(format="%.0f")})
        widget_close()


# ---------------------------------------------------------------------------
# Navigation (barre latérale type « boussole »)
# ---------------------------------------------------------------------------
pages = {
    "explorer": st.Page(page_explorer, title="Product Explorer", icon=":material/account_tree:", default=True),
    "intel": st.Page(page_intelligence, title="Product Intelligence", icon=":material/insights:", url_path="intelligence"),
    "impact": st.Page(page_impact, title="Change Impact", icon=":material/swap_horiz:", url_path="impact"),
    "suppliers": st.Page(page_suppliers, title="Fournisseurs", icon=":material/factory:", url_path="suppliers"),
    "risks": st.Page(page_risks, title="Risques & couverture", icon=":material/shield:", url_path="risks"),
    "bom": st.Page(page_bom_update, title="Mise à jour BOM", icon=":material/playlist_add:", url_path="bom-update"),
    "settings": st.Page(page_settings, title="Paramètres de criticité", icon=":material/tune:", url_path="settings"),
    "quality": st.Page(page_quality, title="Qualité des données", icon=":material/fact_check:", url_path="quality"),
    "assistant": st.Page(page_assistant, title="Assistant PLM", icon=":material/forum:", url_path="assistant"),
}
with st.sidebar:
    st.markdown('<div style="padding:6px 0 12px"><div style="font-size:1.1rem;font-weight:700">PLM Intelligence</div><div style="font-size:.78rem;color:#b7c4d9">Fournisseur e-motor · données OEM</div></div>', unsafe_allow_html=True)
nav = st.navigation({"Analyse": [pages[k] for k in ("explorer", "intel", "impact", "assistant")], "Pilotage": [pages[k] for k in ("suppliers", "risks", "bom", "settings", "quality")]}, position="sidebar")
with st.sidebar:
    st.markdown("---")
    st.markdown(f'<div style="font-size:.75rem;color:#b7c4d9">Export : {html.escape(MODEL.source_name)}<br>{KPI["products"]} objets · {len(BOM)} occurrences<br>Référentiel matières : exemple<br>Ajouts manuels : {len(st.session_state.additions)} · modifications : {len(st.session_state.edits)}</div>', unsafe_allow_html=True)
target = st.session_state.pop("nav_target", None)
if target:
    st.switch_page(pages[target])
nav.run()
