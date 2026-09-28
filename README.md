# Assistant NBA — évaluation et renforcement d'un système RAG

Un prototype d'assistant répond à des questions de fans de NBA à partir de deux
sources : quatre fils Reddit et un classeur de statistiques de saison régulière. Ce
dépôt en mesure la fiabilité, puis corrige les défauts que la mesure établit.

L'état actuel est le **point de départ** : le prototype tel qu'il est fourni, et le
dispositif qui permet de le juger.

---

## Le système aujourd'hui

```
question ──► contrat ──► recherche FAISS ──► 5 fragments ──► mistral-small-latest
                          100 fragments        contrat            │
                            indexés                               ▼
                                                        réponse structurée
                                                    (texte, citations, abstention)
                                                              │
                                                              ▼
                                            citations vérifiées contre les fragments
                                              servis — sinon le modèle est relancé
```

Les documents sont découpés en fragments de 1 500 caractères, vectorisés par
`mistral-embed`, stockés dans un index FAISS. À chaque question, les cinq fragments les
plus proches sont insérés dans un prompt et le modèle rédige.

**Seuls les fils Reddit sont indexés** (`EXTENSIONS_INDEXEES` dans `src/config.py`). Le
classeur en est exclu : la mesure a montré que la feuille contenant les statistiques
n'était jamais récupérée, et que ses fragments prenaient sur les questions mixtes des
places aux fils Reddit. Les chiffres seront atteints autrement.

### Les contrats

Six modèles Pydantic ([src/schemas.py](src/schemas.py)) posés aux frontières :

| Frontière | Ce qui est refusé |
|---|---|
| Documents chargés | extraction vide ou résiduelle, source manquante |
| Fragments découpés | texte vide, identifiant mal formé |
| **Lot d'embeddings** | lot plus court que ses fragments, **vecteur nul**, dimension changée |
| Question | question vide ou démesurée |
| Fragments récupérés | score hors de [-100, 100] |
| **Réponse** | abstention sans motif |

Le contrat sur les embeddings est le plus important : un lot en échec produisait des
vecteurs nuls qui préservaient l'alignement, franchissaient tout contrôle de longueur,
et rendaient les fragments concernés définitivement irrécupérables — leur similarité
valant 0 pour toute question.

**Les citations ne sont pas prises pour argent comptant.** Un validateur de sortie
confronte en Python les identifiants cités aux fragments réellement servis et renvoie
le modèle corriger s'il en invente un. Un contrat Pydantic ne peut pas faire ce
contrôle : il ne connaît pas le contexte du run.

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

Trois runs, 18 cas chacun, aucun échec technique :

| Run | `faithfulness` | `context_precision` | `context_recall` | `answer_correctness` |
|---|---|---|---|---|
| `baseline` | 0.408 | 0.165 | 0.435 | 0.196 |
| `reddit_only` | 0.231 | 0.193 | 0.352 | 0.186 |
| `pydantic_contracts` | **0.784** | 0.165 | 0.324 | **0.268** |

Contrôles qui ne dépendent d'aucun jugement de modèle :

| | `baseline` | `reddit_only` | `pydantic_contracts` |
|---|---|---|---|
| Chiffres corrects (/8) | 0 | 0 | 0 |
| Refus sur les questions sans réponse (/6) | 1 | 0 | **3** |
| Citations invalides | — | — | **0** |

**Ce qui est acquis.** Les contrats et la sortie structurée font passer `faithfulness`
de 0.408 à 0.784, et de 0.036 à 0.705 sur les questions bruitées — là où le prototype
inventait le plus. L'abstention devient un champ exploitable au lieu d'une tournure de
phrase à deviner.

**Ce qui ne l'est pas.** Aucune des huit questions chiffrées n'obtient le bon nombre,
sur aucun des trois runs : la donnée a quitté l'index et rien ne l'a remplacée. Le
système a désormais raison de s'abstenir, ce que le jeu de test compte comme six faux
refus — ils ne deviendront des échecs que si le refus persiste une fois l'accès aux
chiffres rétabli.

**Une limite à connaître.** Sur Reggie Miller, absent des données, le système fabrique
des chiffres **en citant deux fragments parfaitement réels**. Une citation prouve la
provenance de l'identifiant, pas celle du fait.

**Un plancher de bruit, mesuré.** Les runs `reddit_only` et `pydantic_contracts`
partagent exactement les mêmes contextes (18 cas sur 18), et pourtant leurs métriques
de récupération diffèrent de 0.028 — l'écart tenant à un seul cas noté 0.833 puis
0.333. En dessous de ce seuil, un écart n'est pas interprétable.

Le détail et les réserves de méthode sont dans le notebook.

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
  schemas.py                les six contrats Pydantic, aux frontières du système
  loading/loaders.py        extraction du texte (PDF/OCR, Excel, CSV, DOCX, TXT)
  rag/vector_store.py       embeddings, index FAISS, recherche
  rag/generation.py         contexte, prompt, agent, vérification des citations
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
