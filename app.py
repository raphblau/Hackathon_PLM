"""PLM Intelligence — application Streamlit (dashboard + assistant) sur un export XPDMXML.

Lancer :  streamlit run app.py
"""
from __future__ import annotations

import html
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from plm import analytics, assistant, parser

ROOT = Path(__file__).parent
XML_PATH = ROOT / "data" / "XPDMXML_ECAR_PLM.xml"
MAT_PATH = ROOT / "data" / "materials_ref.csv"

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
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
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
MODEL, MATS, RISK, XML_LINES = load_all(st.session_state.xml_upload)
BOM = MODEL.bom
KPI = analytics.kpis(MODEL, RISK)
SUBS = ["Tous"] + BOM.loc[BOM["level"] == 1, "short"].tolist()
st.session_state.setdefault("sel_occ", "148.152")
st.session_state.setdefault("ci_part", "ID_54")
st.session_state.setdefault("ci_mat", "Magnesium alloy AZ91D (HPDC)")
st.session_state.setdefault("chat", [])


def topbar(app: str, ctx: str):
    mode = "Assistant : Claude" if assistant.has_api_key() else "Assistant : mode hors ligne"
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

        widget_open("Nomenclature (BOM)", "Sélectionnez une ligne pour afficher ses propriétés et sa source XML.")
        f1, f2 = st.columns([3, 1])
        q = f1.text_input("Rechercher", placeholder="Pièce, matière, référence, instance…", label_visibility="collapsed")
        show_geo = f2.toggle("Rep. 3D", value=False)
        tbl = BOM if show_geo else BOM.loc[BOM["role"] != "geometry"]
        if sub:
            tbl = tbl.loc[(tbl["level"] == 0) | (tbl["subassembly"] == sub)]
        if q:
            hay = (tbl["short"] + " " + tbl["material"] + " " + tbl["part_number"] + " " + tbl["instance_name"]).str.lower()
            tbl = tbl.loc[hay.str.contains(q.lower(), regex=False)]
        view = pd.DataFrame({
            "Composant": tbl.apply(lambda r: (" " * r["level"]) + ("▸ " if r["role"] == "assembly" else "") + r["short"] + (" [3D]" if r["role"] == "geometry" else ""), axis=1),
            "Rév.": tbl["rev"], "Maturité": tbl["maturity"], "Matière": tbl["material"].str.replace(r"\s*\(.*?\)", "", regex=True),
            "Masse (kg)": tbl["mass_kg"], "occ": tbl["occ"],
        })
        ev = st.dataframe(
            view, hide_index=True, height=420, on_select="rerun", selection_mode="single-row", width="stretch",
            column_config={"occ": None, "Masse (kg)": st.column_config.NumberColumn(format="%.3f"), "Composant": st.column_config.TextColumn(width="medium")},
            key="bom_table",
        )
        if ev.selection.rows:
            st.session_state.sel_occ = view.iloc[ev.selection.rows[0]]["occ"]
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
        st.markdown(f"**{html.escape(r['name'])}** &nbsp; {chip(r['role_label'], 'fill')} {chip(r['maturity'], 'warn' if r['maturity'] != 'RELEASED' else 'ok')}", unsafe_allow_html=True)
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
            props += [("Coût estimé*", fmt_eur(k["est_cost"])), ("Risque appro*", k["risk_label"]), ("Fournisseur*", k["supplier_category"]),
                      ("Criticité*", f"{k['score']} / 100 · {k['tier']}")]
        st.markdown('<div class="plm-props">' + "".join(f'<div class="k">{html.escape(a)}</div><div class="v">{html.escape(str(b))}</div>' for a, b in props) + "</div>", unsafe_allow_html=True)
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

    widget_open("Criticité des composants",
                "Score sur 100 : 40 % risque d'approvisionnement de la matière, 30 % part du coût, 20 % part de la masse, 10 % occurrences, +5 points par révision au-delà de A (max +15). Critique ≥ 60, à surveiller ≥ 35.")
    view = r[["short", "rev", "subassembly", "material", "qty", "total_mass", "est_cost", "risk_label", "supplier_category", "kind", "score", "tier", "xml_id"]].copy()
    view["rev"] = view.apply(lambda x: f"{x['rev']} ⚠" if x["rev"] >= "C" else x["rev"], axis=1)
    view = view.rename(columns={"short": "Composant", "rev": "Rév.", "subassembly": "Sous-ensemble", "material": "Matière", "qty": "Qté", "total_mass": "Masse (kg)",
                                "est_cost": "Coût est. (€)", "risk_label": "Risque appro", "supplier_category": "Fournisseur (cat.)", "kind": "Type", "score": "Score", "tier": "Niveau"})
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

    widget_open("Dépendances fournisseurs", "Catégories déduites de la matière et du procédé (hypothèse). Une catégorie à risque élevé qui porte une grosse part du coût est une dépendance à sécuriser.")
    sv = bs.rename(columns={"supplier_category": "Catégorie de fournisseur", "kind": "Type", "refs": "Réf.", "qty": "Pièces", "mass": "Masse (kg)", "cost": "Coût est. (€)", "cost_share": "Part du coût", "risk_label": "Risque max"}).drop(columns="risk")
    st.dataframe(sv, hide_index=True, width="stretch", column_config={"Part du coût": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
                                                                       "Masse (kg)": st.column_config.NumberColumn(format="%.2f"), "Coût est. (€)": st.column_config.NumberColumn(format="%.0f")})
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
    st.markdown(f"Matière actuelle : **{html.escape(a['material'])}** ({a['family']}) · fournisseur actuel : {html.escape(a['supplier_category'])} · {ci['qty']} occurrence(s) · masse unitaire {fmt_kg(ci['old_unit_mass'])} kg → {fmt_kg(ci['new_unit_mass'])} kg", unsafe_allow_html=True)

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
    widget_open("Charger un autre export", "Le même traitement s'applique à tout fichier XPDMXML (structure produit 3DEXPERIENCE).")
    up = st.file_uploader("Fichier XPDMXML", type=["xml"], label_visibility="collapsed")
    if up is not None and st.button("Analyser ce fichier"):
        st.session_state.xml_upload = up.getvalue()
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
    tb = assistant.Toolbox(MODEL, MATS)
    online = assistant.has_api_key()
    if online:
        st.caption(f"Mode Claude ({assistant.MODEL_ID}) : le modèle choisit les outils à appeler, les résultats portent leurs références XML.")
    else:
        st.info("Aucune clé `ANTHROPIC_API_KEY` : l'assistant fonctionne en **mode hors ligne** (routage par mots-clés vers les mêmes outils). Définissez la clé pour des réponses en langage naturel.", icon="ℹ️")

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
                    if online:
                        text, calls, hist = assistant.ask_claude(tb, st.session_state.get("claude_history", []), q)
                        st.session_state.claude_history = hist
                    else:
                        text, calls = assistant.ask_offline(tb, q)
                except Exception as e:  # clé invalide, réseau…
                    text, calls = f"Erreur lors de l'appel au modèle : {e}\n\nRéponse en mode hors ligne :\n\n" + assistant.ask_offline(tb, q)[0], []
            st.markdown(text)
        st.session_state.chat.append({"role": "assistant", "content": text, "calls": [{"name": c.name, "args": c.args, "refs": c.refs} for c in calls]})
        st.rerun()
    if st.session_state.chat and st.button("Effacer la conversation"):
        st.session_state.chat, st.session_state.claude_history = [], []
        st.rerun()


# ---------------------------------------------------------------------------
# Navigation (barre latérale type « boussole »)
# ---------------------------------------------------------------------------
pages = {
    "explorer": st.Page(page_explorer, title="Product Explorer", icon=":material/account_tree:", default=True),
    "intel": st.Page(page_intelligence, title="Product Intelligence", icon=":material/insights:", url_path="intelligence"),
    "impact": st.Page(page_impact, title="Change Impact", icon=":material/swap_horiz:", url_path="impact"),
    "quality": st.Page(page_quality, title="Qualité des données", icon=":material/fact_check:", url_path="quality"),
    "assistant": st.Page(page_assistant, title="Assistant PLM", icon=":material/forum:", url_path="assistant"),
}
with st.sidebar:
    st.markdown('<div style="padding:6px 0 12px"><div style="font-size:1.1rem;font-weight:700">PLM Intelligence</div><div style="font-size:.78rem;color:#b7c4d9">Fournisseur e-motor · données OEM</div></div>', unsafe_allow_html=True)
nav = st.navigation({"Applications": list(pages.values())}, position="sidebar")
with st.sidebar:
    st.markdown("---")
    st.markdown(f'<div style="font-size:.75rem;color:#b7c4d9">Export : {html.escape(MODEL.source_name)}<br>{KPI["products"]} objets · {len(BOM)} occurrences<br>Référentiel matières : exemple</div>', unsafe_allow_html=True)
target = st.session_state.pop("nav_target", None)
if target:
    st.switch_page(pages[target])
nav.run()
