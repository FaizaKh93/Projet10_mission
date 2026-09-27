# Assistant NBA — évaluation et renforcement d'un système RAG

Un prototype d'assistant répond à des questions de fans de NBA à partir de deux
sources : quatre fils Reddit et un classeur de statistiques de saison régulière. Ce
dépôt en mesure la fiabilité, puis corrige les défauts que la mesure établit.

L'état actuel est le **point de départ** : le prototype tel qu'il est fourni, et le
dispositif qui permet de le juger.

---

## Le système aujourd'hui

```
question ──► recherche FAISS ──► 5 fragments ──► mistral-small-latest ──► texte libre
              302 fragments indexés
```

Les documents sont découpés en fragments de 1 500 caractères, vectorisés par
`mistral-embed`, stockés dans un index FAISS. À chaque question, les cinq fragments les
plus proches sont insérés dans un prompt et le modèle rédige.

Ni validation des entrées, ni sortie structurée, ni citation vérifiable, ni mécanisme
d'abstention — c'est ce que l'évaluation sert à objectiver.

---

## Installation

Prérequis : Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), une clé API **Mistral**
(système évalué) et une clé **OpenAI** (juge de l'évaluation).

```bash
git clone https://github.com/FaizaKh93/Projet10_mission.git
cd Projet10_mission
uv sync
```

`.env` à la racine :

```
MISTRAL_API_KEY=...
OPENAI_API_KEY=...
```

## Indexer, puis lancer

Les sources sont dans `data/inputs/`. Les PDF sont des captures d'écran de
discussions, donc passés à l'OCR : la première indexation télécharge le modèle EasyOCR
et prend plusieurs minutes.

```bash
uv run python scripts/index.py            # construit data/vector_db/ — appels API facturés
uv run streamlit run app/chat.py          # lance l'assistant
```

`data/vector_db/` n'est pas versionné : il se régénère.

---

## Évaluer

### Le jeu de test

`eval/testset.json` — 18 questions dont la réponse attendue a été vérifiée à la main
dans les sources, croisant deux axes à six cas par valeur :

| | Reddit (PDF) | Excel | hybride |
|---|---|---|---|
| **simple** | un fait à retrouver | une valeur à lire | identifier puis chiffrer |
| **complexe** | synthèse de points de vue | filtrage, classement, agrégation | candidats du texte, comparaison chiffrée |
| **bruitée** | information absente, question subjective | granularité ou dimension inexistante | conflit de présence entre sources |

Les six cas **bruités** n'ont pas de bonne réponse : la seule conduite correcte est de
signaler qu'on ne peut pas répondre. Ils mesurent la résistance à l'invention, que les
douze autres ne voient pas.

### Lancer un run

```bash
uv run python eval/evaluate_ragas.py --label baseline
```

Quatre métriques RAGAS jugées par `gpt-4o` — différent du modèle évalué, pour qu'aucun
modèle ne se note lui-même :

| Métrique | Question posée |
|---|---|
| `faithfulness` | la réponse invente-t-elle des faits absents du contexte ? |
| `context_precision` | les fragments récupérés sont-ils pertinents ? |
| `context_recall` | le contexte contient-il de quoi répondre ? |
| `answer_correctness` | la réponse est-elle la bonne ? |

`--limit N` pour n'exécuter que les N premiers cas, `--force` pour écraser un run
existant. Les résultats sont écrits **après chaque cas** : une interruption ne perd pas
ce qui précède. Les deux API sont facturées.

### Lire les résultats

`eval/analyze_results.ipynb` est versionné avec ses sorties : il se lit sans être
exécuté.

---

## Ce que l'évaluation établit

Run `baseline`, 18 cas, aucun échec technique :

| `faithfulness` | `context_precision` | `context_recall` | `answer_correctness` |
|---|---|---|---|
| 0.408 | 0.165 | 0.435 | 0.196 |

Trois constats qui ne dépendent d'aucun jugement de modèle :

1. **Aucune des huit questions chiffrées n'obtient le bon nombre** — contrôle par
   recherche de la valeur attendue dans la réponse : 0 sur 8.
2. **La feuille contenant les statistiques n'est jamais récupérée**, bien qu'elle
   occupe 143 des 302 fragments de l'index. Ce sont les feuilles qui *décrivent* les
   données qui sortent à sa place.
3. **Cinq des six questions bruitées reçoivent une réponse affirmative et fausse**,
   dont un total de points entièrement fabriqué pour un joueur absent des données.

Le détail, les réserves de méthode et les limites de chaque métrique sont dans le
notebook.

---

## Tests

```bash
uv run pytest            # tests gratuits
uv run pytest -m api     # appels réels à l'API Mistral (facturés)
```

Les tests `api` sont exclus par défaut et sautés si `MISTRAL_API_KEY` n'est pas
définie. Couverture : `uv run pytest --cov --cov-report=html`.

---

## Structure

```
app/chat.py                 interface Streamlit
scripts/index.py            construction de l'index vectoriel
src/
  config.py                 chemins, modèles, paramètres de découpage et de recherche
  loading/loaders.py        extraction du texte (PDF/OCR, Excel, CSV, DOCX, TXT)
  rag/vector_store.py       embeddings, index FAISS, recherche
  rag/generation.py         contexte, prompt, appel du modèle
eval/
  testset.json              les 18 cas
  evaluate_ragas.py         exécution du système + notation RAGAS
  analyze_results.ipynb     analyse des résultats
  results/                  un fichier JSON par run
tests/                      tests unitaires
notebooks/                  exploration des données sources
data/inputs/                PDF Reddit et classeur Excel
```

`src/rag/generation.py` est partagé par l'interface et par l'évaluation : le prompt et
l'appel au modèle n'existent qu'à un seul endroit. Sans cela, l'évaluation pourrait
mesurer autre chose que ce que l'application fait réellement.
