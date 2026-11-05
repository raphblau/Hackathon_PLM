# PLM Intelligence — ECAR e-drive

Application Streamlit (dashboard + assistant) qui exploite un export **XPDMXML** de 3DEXPERIENCE
reçu de l'OEM, pour répondre aux trois missions du hackathon : **Product Explorer**,
**Product Intelligence**, **Change Impact Analysis**, plus un **assistant conversationnel**
qui cite ses sources PLM.

## Lancer l'application

```bash
pip install -r requirements.txt
streamlit run app.py
```

Pour l'assistant en langage naturel (Claude), définissez une clé avant de lancer :

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Sans clé, l'assistant fonctionne en **mode hors ligne** : un routeur par mots-clés appelle les
mêmes outils, les réponses sont plus mécaniques mais toujours sourcées.

## Pages

| Page | Contenu |
|---|---|
| Product Explorer | Sunburst des masses, nomenclature, propriétés et source XML, où est utilisée la pièce |
| Product Intelligence | Matières, criticité (pondérations modifiables), choc de prix, fournisseurs, marché matières (décomposition, évolution du coût, prévision d'indices) |
| Change Impact | Impact d'un changement de matière, objets PLM à réviser, brouillon d'ECR |
| Assistant PLM | Chat avec outils et citations XML (Claude ou mode hors ligne) |
| Fournisseurs | Catalogue (`data/suppliers.csv`), contrats et échéances, ESG, délais de réponse, carte des sites, pièces nominées |
| Risques & couverture | Registre (risques déduits + saisis), matrice probabilité × impact, options de couverture : forward/futures, matière alternative, seconde source, contrat, stock |
| Mise à jour BOM | Ajout d'un composant / sous-assemblage / lot CSV, superposé à l'export OEM, avec prix saisi |
| Paramètres de criticité | Critères, pondérations, malus (révisions, contrats, ESG) et seuils, aperçu en direct, export du profil |
| Qualité des données | Contrôles de l'export, en-tête, chargement d'un autre XML |

## Structure

| Fichier | Rôle |
|---|---|
| `plm/parser.py` | Lecture du XPDMXML → nomenclature aplatie (une ligne par occurrence, avec la ligne XML d'origine) |
| `plm/analytics.py` | Criticité, matières, fournisseurs, choc de prix, impact d'un changement, contrôles qualité |
| `plm/assistant.py` | Outils interrogeables + boucle agentique Claude (`claude-opus-5-5`) + mode hors ligne |
| `app.py` | Interface Streamlit (9 pages, habillage type plateforme PLM) |
| `data/XPDMXML_ECAR_PLM.xml` | Export OEM |
| `data/materials_ref.csv` | **Référentiel matières d'exemple** : prix, densité, CO₂, risque appro, facteur procédé, fournisseur |
| `data/suppliers.csv` | **Catalogue fournisseurs d'exemple** : catégorie, site, note, ESG, délai de réponse, contrat (début, fin, type), capacité |
| `plm/extras.py` | Contrats, registre de risques, options de couverture, simulation de prix, ajouts à la nomenclature |

## Ce qui vient du XML et ce qui est une hypothèse

- **Du XML** : structure, références, révisions, maturité, cycle de vie, dates, propriétaire, masse, matière, représentations 3D.
- **Catalogue fournisseurs (`suppliers.csv`)** : données d'exemple du fournisseur (noms fictifs). Les prévisions d'indices de prix sont une **simulation** déterministe (dérive et volatilité par famille), pas des cours de marché.
- **Hypothèses (`materials_ref.csv`)** : prix €/kg, facteur procédé, CO₂, risque d'approvisionnement, catégorie de fournisseur. Ce sont des ordres de grandeur, à remplacer par les données achats. Tout chiffre qui en dépend est marqué « hypothèse » dans l'interface.

## Méthodes

- **Coût estimé** = masse × prix matière × facteur procédé.
- **Score de criticité (0-100)** : score de base = parts normalisées (par défaut 40 % risque matière, 30 % coût, 20 % masse, 10 % occurrences) ; malus : +5 points par révision au-delà de A (max +15), +10 si le contrat du fournisseur expire sous 6 mois, +5 si l'ESG du fournisseur est sous 60. Critique ≥ 60, à surveiller ≥ 35. Tout est modifiable dans « Paramètres de criticité » et exportable en JSON.
- **Choc de prix** : s'applique à la part matière du coût (coût ÷ facteur procédé).
- **Impact d'un changement** : volume constant (masse × densité nouvelle / densité actuelle) ; chaque assemblage parent passe à la révision suivante.

## Images des composants

L'export OEM ne contient aucune géométrie (`WithFiles = No`, `WithDocuments = No`). L'application affiche donc
un **pictogramme illustratif** par type de composant (roulement, engrenage, arbre, carter, vis, stator, bobinage…),
dessiné en SVG dans `plm/icons.py`, et le dit explicitement. Pour afficher la vraie pièce, il faut demander à l'OEM
un export avec fichiers 3D (STEP ou 3DXML) ou des vignettes : le panneau de propriétés peut alors les montrer.

## Traçabilité

Chaque composant affiche l'identifiant de son objet `Product` et de son instance `ProductInst`,
avec le **numéro de ligne du fichier XML**, et un extrait de la source brute. L'assistant ne voit
jamais le XML : il n'accède aux données que par des outils dont chaque résultat porte ces références,
et chaque réponse se termine par une liste « Sources ».
