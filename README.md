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

## Structure

| Fichier | Rôle |
|---|---|
| `plm/parser.py` | Lecture du XPDMXML → nomenclature aplatie (une ligne par occurrence, avec la ligne XML d'origine) |
| `plm/analytics.py` | Criticité, matières, fournisseurs, choc de prix, impact d'un changement, contrôles qualité |
| `plm/assistant.py` | Outils interrogeables + boucle agentique Claude (`claude-opus-5-5`) + mode hors ligne |
| `app.py` | Interface Streamlit (5 pages, habillage type plateforme PLM) |
| `data/XPDMXML_ECAR_PLM.xml` | Export OEM |
| `data/materials_ref.csv` | **Référentiel matières d'exemple** : prix, densité, CO₂, risque appro, facteur procédé, fournisseur |

## Ce qui vient du XML et ce qui est une hypothèse

- **Du XML** : structure, références, révisions, maturité, cycle de vie, dates, propriétaire, masse, matière, représentations 3D.
- **Hypothèses (`materials_ref.csv`)** : prix €/kg, facteur procédé, CO₂, risque d'approvisionnement, catégorie de fournisseur. Ce sont des ordres de grandeur, à remplacer par les données achats. Tout chiffre qui en dépend est marqué « hypothèse » dans l'interface.

## Méthodes

- **Coût estimé** = masse × prix matière × facteur procédé.
- **Score de criticité (0-100)** = 40 % risque matière + 30 % part du coût + 20 % part de la masse + 10 % occurrences, +5 points par révision au-delà de A (max +15). Critique ≥ 60, à surveiller ≥ 35.
- **Choc de prix** : s'applique à la part matière du coût (coût ÷ facteur procédé).
- **Impact d'un changement** : volume constant (masse × densité nouvelle / densité actuelle) ; chaque assemblage parent passe à la révision suivante.

## Traçabilité

Chaque composant affiche l'identifiant de son objet `Product` et de son instance `ProductInst`,
avec le **numéro de ligne du fichier XML**, et un extrait de la source brute. L'assistant ne voit
jamais le XML : il n'accède aux données que par des outils dont chaque résultat porte ces références,
et chaque réponse se termine par une liste « Sources ».
