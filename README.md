# Assistant NBA — évaluation et renforcement d'un système RAG

Un prototype d'assistant répond à des questions de fans de NBA à partir de deux
sources : quatre fils Reddit et un classeur de statistiques de saison régulière. Ce
dépôt en mesure la fiabilité, puis corrige les défauts que la mesure établit.

Quatre évaluations jalonnent le travail. La dernière mesure l'effet de la base
relationnelle et du routage : **5 chiffres exacts sur 8, contre 0 sur 8 au départ**.

---

## Le système aujourd'hui

```
question
   │
   ▼
[1] routage        un premier appel au modèle choisit la source, avant toute collecte
   │                   documents · base · les_deux · aucune
   ▼
[2] collecte       recherche FAISS, lancée seulement si la route l'inclut
   │                   100 fragments indexés, 5 servis
   ▼
[3] réponse        mistral-small-latest, avec l'outil SQL offert seulement
   │               si la route l'inclut
   ▼
réponse structurée (texte, citations, abstention)
   │
   ▼
citations vérifiées contre les fragments servis ET les résultats SQL
— sinon le modèle est relancé
```

Un seul point d'entrée, [`repondre()`](src/rag/pipeline.py), est appelé à l'identique
par l'interface et par l'évaluation : ce qui est mesuré est ce que l'application fait.

Le principe qui tient l'ensemble : **le modèle propose, le code dispose.** Le routeur
choisit la source mais ne collecte pas ; le modèle écrit le SQL mais ne l'exécute pas ;
il cite ses sources mais ses citations sont vérifiées.

Les documents sont découpés en fragments de 1 500 caractères, vectorisés par
`mistral-embed`, stockés dans un index FAISS. **Seuls les fils Reddit y sont indexés**
(`EXTENSIONS_INDEXEES` dans `src/config.py`) : la mesure a montré que la feuille
contenant les statistiques n'était jamais récupérée, et que ses fragments prenaient sur
les questions mixtes des places aux fils Reddit. Les chiffres viennent désormais d'une
base relationnelle.

### Les contrats

Des modèles Pydantic ([src/schemas.py](src/schemas.py)) posés à chaque frontière. Sur
le chemin documentaire :

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

Le chemin chiffré a les siens : une ligne du classeur et un résultat de requête sont
validés de la même manière, au même endroit.

**Les citations ne sont pas prises pour argent comptant.** Un validateur de sortie
confronte en Python les identifiants cités aux fragments réellement servis — extraits
documentaires comme résultats de requête — et renvoie le modèle corriger s'il en
invente un. Un contrat Pydantic ne peut pas faire ce contrôle : il ne connaît pas le
contexte du run.

---

## Les chiffres : une base relationnelle

Les deux sources sont chargées dans SQLite ([src/db_models.py](src/db_models.py)), en
tables **STRICT** — SQLite refuse alors une valeur du mauvais type au lieu de la
convertir :

| Table | Contenu | Interrogeable |
|---|---|---|
| `teams` | 30 franchises | oui |
| `players` | 569 joueurs | oui |
| `stats` | 569 lignes × 47 colonnes, saison régulière | oui |
| `reports` | 4 fils Reddit, texte intégral | **non** |

`reports` fait doublon avec l'index FAISS, et c'est voulu : un `LIKE` n'a pas de sens
sémantique, les questions narratives restent servies par la recherche vectorielle. La
table expose les sources sous forme relationnelle, sans être offerte au modèle.

**Pourquoi il n'y a pas de table `matches`.** Le modèle relationnel en prévoyait une.
Le classeur n'en contient pas la matière : ses cinq feuilles — `Données NBA`,
`Analyse`, `Analyse Vide`, `Equipe`, `Dictionnaire des données` — ne donnent qu'un
agrégat **par joueur et par saison**, une ligne par joueur, aucune ligne par match. La
créer l'aurait laissée vide, et une table vide ment deux fois : au modèle, qui croit
pouvoir l'interroger, et à qui lit le schéma.

**Ce que cette absence interdit.** Pas de date, pas d'adversaire, **pas de distinction
domicile/extérieur** — et une seule saison en base, donc aucune comparaison d'une
saison à l'autre. « Quelle équipe a le meilleur bilan à domicile ? » n'a pas de réponse
ici, et la seule conduite correcte est de le dire plutôt que de répondre sur le bilan
global.

Ces limites ne sont pas recopiées à la main : `profil_capacites()` les **dérive** de la
base et les transmet au routeur comme au modèle. Le jour où la donnée par match
existera, la phrase disparaîtra d'elle-même.

Chaque ligne est validée par Pydantic **avant** insertion. Une ligne fautive est
écartée avec son motif, les autres passent : une anomalie ponctuelle ne doit pas priver
la base des 568 autres joueurs.

L'ingestion est contrôlée contre une source qui ne vient pas de notre code : la feuille
`Analyse` du classeur porte ses propres totaux par équipe, et la base les reproduit à
l'unité près (OKC 9 880 points pour 18 joueurs, MIA 9 828 pour 19, CLE 10 180 pour 18).

### L'outil SQL

Le modèle écrit la requête ; [src/rag/sql_tool.py](src/rag/sql_tool.py) l'exécute sans
lui faire confiance. Quatre barrières, dont trois appliquées par SQLite lui-même plutôt
que par inspection du texte — qu'un commentaire ou une CTE contournerait :

| Barrière | Ce qu'elle empêche |
|---|---|
| connexion `mode=ro` | toute écriture, quelle que soit la requête |
| autoriseur SQLite | tout ce qui n'est pas une lecture des tables autorisées |
| gestionnaire de progression | une requête qui dépasse 5 s |
| limites de taille | un résultat ou une cellule qui ferait exploser le prompt |

La liste des tables est **blanche** : une table ajoutée demain sera refusée par défaut,
là où une liste noire l'exposerait.

**Pourquoi pas la chaîne SQL clés en main de LangChain.** `SQLDatabaseToolkit` et
`create_sql_agent` exécutent eux-mêmes la requête écrite par le modèle — donc hors de
`mode=ro`, de l'autoriseur, du délai et des bornes de taille. Les adopter revenait à
supprimer les quatre barrières. LangChain sert donc ici à ce qu'il fait bien :
**décrire**. `SQLDatabase.get_table_info()` rend les `CREATE TABLE` réels et deux
lignes d'exemple par table, ce qui ancre le modèle dans les vrais noms de colonnes.
L'exécution, elle, reste dans `executer_sql()`.

Ce que le modèle reçoit avec l'outil est **dérivé de la base, jamais écrit à la main** :
le schéma réel, des exemples de requêtes, les limites de la base — et les pièges
d'agrégation. Une recopie manuelle finit toujours par mentir ; et sans le schéma, le
modèle devine les noms de colonnes, puis épuise ses relances.

Une requête refusée n'est pas une erreur fatale : son motif est renvoyé au modèle, qui
corrige et réessaie. Mieux qu'un résultat vide, qu'il interpréterait comme « 0 ».

### Le routage

Un premier appel au modèle ([src/rag/pipeline.py](src/rag/pipeline.py)) choisit la
source avant toute collecte, et rend sa décision **et son motif** — une route est donc
vérifiable après coup.

Sa consigne est construite à chaque appel à partir du profil réel de la base : tables
présentes, colonnes, cardinalités. Le routeur sait ainsi qu'il n'y a ni granularité par
match, ni comparaison entre saisons, sans que personne ne l'ait écrit.

Quand la route exclut la base, **l'outil SQL n'est pas déconseillé au modèle : il lui
est retiré.** Une consigne dans un prompt ne contraint rien ; l'absence de l'outil, si.
Symétriquement, une route `base` n'engage aucun embedding facturé pour un contexte qui
ne servirait pas.

Deux limites assumées. Décider avant de chercher, c'est décider sans savoir ce qu'on
trouvera : l'abstention reste la responsabilité du nœud de réponse. Et une panne du
routeur replie sur `les_deux` — une collecte de plus, mais aucune source perdue.

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
uv run python scripts/index.py                # construit data/vector_db/ — appels API facturés
uv run python scripts/load_excel_to_db.py     # tables teams / players / stats
uv run python scripts/load_reports_to_db.py   # table reports — reprend le texte de l'index
uv run streamlit run app/chat.py              # l'assistant, en interface web
uv run uvicorn app.api:app --reload           # le même assistant, en HTTP
```

`data/vector_db/` et `data/nba.db` ne sont pas versionnés : ils se régénèrent.

`load_reports_to_db.py` lit le texte déjà extrait par l'indexation plutôt que de
relancer l'OCR : quelques secondes au lieu d'une dizaine de minutes. L'OCR reste en
recours pour un PDF absent de l'index.

---

## L'API REST

Même pipeline que l'interface Streamlit, autre transport : les deux appellent
`repondre()`. Une réponse servie en HTTP est donc celle que l'évaluation mesure.

```bash
uv run uvicorn app.api:app --reload
```

| Endpoint | Rôle |
|---|---|
| `GET /` | identité du service et liste des endpoints |
| `GET /health` | *liveness* — le processus répond, aucune dépendance vérifiée |
| `GET /ready` | *readiness* — index, base et clé API ; **503** si l'une manque |
| `POST /ask` | poser une question |
| `GET /logs` | les 100 derniers appels |

La documentation interactive complète est sur **`/docs`**, générée par FastAPI depuis
les modèles Pydantic — les schémas ne sont donc écrits qu'une fois, dans le code.

### Poser une question

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Combien de points Nikola Jokić a-t-il marqués cette saison ?"}'
```

```json
{
  "reponse": {
    "answer": "Nikola Jokić a marqué un total de 2 072 points cette saison.",
    "citations": ["sql_1"],
    "abstain": false,
    "abstain_reason": null
  },
  "route": "base",
  "route_motif": "La question demande un chiffre total de points marqués par un joueur sur une saison, ce que la base de données peut fournir.",
  "requetes": [
    {
      "requete": "SELECT s.pts_total FROM stats s JOIN players p ON p.player_id = s.player_id WHERE p.full_name = 'Nikola Jokić'",
      "colonnes": ["pts_total"],
      "lignes": [[2072]],
      "tronque": false
    }
  ],
  "latence_ms": {"total": 1412.6}
}
```

**`route` et `requetes` ne sont pas décoratifs.** Sans eux, l'appelant devrait croire le
modèle sur parole ; avec eux, il peut rejouer la requête et vérifier le chiffre. La
décomposition fine de la latence n'est pas dans la réponse : elle vit dans la trace
Logfire, qui ouvre un span par étape.

### Quand la réponse n'existe pas

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Combien de points Reggie Miller a-t-il marqués cette saison ?"}'
```

```json
{
  "reponse": {
    "answer": "Je n'ai pas pu trouver Reggie Miller dans la base de données des joueurs.",
    "citations": [],
    "abstain": true,
    "abstain_reason": "Reggie Miller n'apparaît pas dans la base de données des joueurs pour la saison disponible."
  },
  "route": "base",
  "requetes": [{"requete": "SELECT s.pts_total FROM stats s JOIN players p ...", "lignes": []}]
}
```

**Une abstention est un succès : HTTP 200.** Le traitement est allé au bout et a conclu
qu'il ne pouvait pas répondre. La compter comme une panne fausserait toute lecture des
journaux. La requête à zéro ligne est ce qui **prouve** l'absence.

### Vérifier que le service est prêt

```bash
curl http://localhost:8000/ready
```

```json
{"status": "ready",
 "verifications": {"index_vectoriel": true, "base_nba": true, "cle_mistral": true}}
```

Si l'une des trois manque, `/ready` rend **503** et nomme laquelle. C'est cet endpoint
qu'un orchestrateur interroge avant de router du trafic ; `/health` ne dit que la vie du
processus.

### Codes de retour

| Code | Sur `/ask` |
|---|---|
| `200` | traitement abouti — **y compris une abstention** |
| `422` | question vide, trop courte ou de plus de 500 caractères — rejetée par Pydantic **avant** tout appel facturé |
| `503` | index vectoriel indisponible |
| `500` | panne pendant le traitement — journalisée avec son type d'exception |

Sous PowerShell, `curl` est un alias d'`Invoke-WebRequest` : utiliser `curl.exe` ou Git
Bash pour que les exemples ci-dessus fonctionnent tels quels.

**`/logs` expose les questions et les réponses.** Fenêtre de débogage, volatile et
propre au processus — à protéger avant toute mise en production.

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

**Exécution et notation sont enregistrées séparément.** Le système répond (Mistral),
puis le juge note (OpenAI). Une panne du juge — quota épuisé, limite de débit — n'efface plus
la réponse déjà payée : elle est conservée avec un champ `error_notation`, et se rattrape
sans relancer le système :

```bash
uv run python eval/evaluate_ragas.py --label sql_routing --renoter
```

**Aucun appel Mistral** : ni routage, ni recherche, ni requête SQL. Les cas déjà notés
sont ignorés, donc la commande se relance autant de fois que nécessaire — elle avance à
chaque passage. C'est ce qui a sauvé le 4ᵉ run, interrompu par un quota OpenAI épuisé
après 18 réponses déjà produites.

### Lire les résultats

`eval/analyze_results.ipynb` est versionné avec ses sorties : il se lit sans être
exécuté.

---

## Ce que l'évaluation établit

Quatre runs, 18 cas chacun, aucun échec système :

| Run | `faithfulness` | `context_precision` | `context_recall` | `answer_correctness` |
|---|---|---|---|---|
| `baseline` | 0.408 | 0.165 | 0.435 | 0.196 |
| `reddit_only` | 0.231 | 0.193 | 0.352 | 0.186 |
| `pydantic_contracts` | **0.784** | 0.165 | 0.324 | 0.268 |
| `sql_routing` | 0.713 | 0.554 | 0.602 | **0.396** |

Contrôles qui ne dépendent d'aucun jugement de modèle :

| | `baseline` | `reddit_only` | `pydantic_contracts` | `sql_routing` |
|---|---|---|---|---|
| Chiffres corrects (/8) | 0 | 0 | 0 | **5** |
| Routage correct (/18) | — | — | — | **15** |
| Refus sur les questions sans réponse (/6) | 1 | 0 | **3** | 2 |
| Citations invalides | — | — | 0 | 0 |

**Les chiffres, enfin.** **5 sur 8, contre 0 sur 8 aux trois runs précédents** — et
**4/4** sur les questions qui tiennent en une requête directe. `answer_correctness`
passe de 0.268 à **0.396**, et de 0.262 à **0.598** sur les questions Excel. C'était le
manque central du prototype.

**L'ancrage, acquis plus tôt.** Les contrats et la sortie structurée avaient fait passer
`faithfulness` de 0.408 à 0.784, et de 0.036 à 0.705 sur les questions bruitées. Le
quatrième run le fait reculer à 0.713 — au-dessus du plancher de bruit, donc réel.

**Une absence devient démontrable.** Interrogé sur Reggie Miller, absent des données, le
système fabriquait des chiffres — au troisième run **en citant deux fragments
parfaitement réels**. Au quatrième, la requête rend **zéro ligne** et il s'abstient avec
un motif exact. Une requête vide est une **preuve d'absence** ; aucun corpus
documentaire ne peut en fournir, car un corpus muet est indiscernable d'une recherche
ratée.

**Le chemin hybride reste faible : 1/4.** Identifier quelqu'un dans les discussions
*puis* chercher son chiffre est l'enchaînement qui casse. Les trois erreurs de routage
sont toutes des hybrides, et recoupent exactement les trois chiffres manqués.

**Et les refus reculent, 3/6 à 2/6.** Là où le système se taisait faute d'accès, il
interroge et répond à côté. Deux cas sont instructifs, et **aucun n'invente hors des
sources** :

- **B4** — on demande une comparaison domicile/extérieur, dimension inexistante. Le
  modèle écrit `CASE WHEN team_code IN (...) THEN 'Domicile'` et **fabrique la
  dimension** en SQL.
- **B6** — `SELECT MAX(s.wins_total) AS total_points` : il interroge les victoires et
  annonce « 49 points marqués en playoffs ».

Ces deux limites sont **identifiées et non résolues**. La description transmise à
l'outil énonce pourtant deux fois qu'il n'y a pas de distinction domicile/extérieur : le
défaut n'est donc pas un manque d'information, c'est qu'**une information ne contraint
pas**. Ajouter une consigne de plus serait un correctif non mesuré.

**Le biais du mapping NL→SQL.** Les trois erreurs de routage sont des questions
formulées en **« combien de… »**, où l'étape d'identification dans les documents est
implicite. Le routeur s'arrête à la forme de surface — ses propres motifs le disent :
*« demande un chiffre précis qui ne peut être obtenu que via la base »*. Il décide sur
les **limites** de la base, jamais sur ses colonnes : bon niveau d'abstraction, mais
angle mort assumé.

**Deux métriques qui ne se comparent pas.** `context_precision` et `context_recall`
bondissent au quatrième run (0.165 → 0.554), mais **par changement d'unité** : cinq
extraits flous d'un côté, une ligne de base exacte de l'autre. Le contrôle est net — sur
les questions Reddit, où le mécanisme n'a pas bougé, elles valent **0.495 et 0.639 dans
les deux runs, au centième près**. Tout le gain vient des questions Excel. Ce n'est pas
une meilleure recherche.

**Un plancher de bruit, mesuré.** Les runs `reddit_only` et `pydantic_contracts`
partagent exactement les mêmes contextes (18 cas sur 18), et pourtant leurs métriques
de récupération diffèrent de 0.028 — l'écart tenant à un seul cas noté 0.833 puis
0.333. En dessous de ce seuil, un écart n'est pas interprétable.

Le détail et les réserves de méthode sont dans le notebook.

---

## Observabilité

[Pydantic Logfire](https://pydantic.dev/logfire) trace la chaîne pas à pas. Chaque
question produit un arbre de spans :

```
cas  (id=S2)                        ← un span par question, pendant une évaluation
  agent run                         ← [1] le routage
    chat mistral-small…                 la source choisie, et son motif
  recherche_faiss                   ← [2] seulement si la route inclut les documents
  agent run                         ← [3] la génération
    chat mistral-small…
    running tool: interroger_base   ← la requête SQL écrite par le modèle
    chat mistral-small…             ← relance : citation refusée, ou requête refusée
```

Ce qu'on y voit et que les logs ne donnaient pas : **la route effectivement prise**,
**le SQL réellement exécuté**, **la relance du validateur de citations**, le temps
passé dans la recherche par rapport à la génération, et le contenu exact envoyé au
modèle.

L'agent est instrumenté automatiquement (`logfire.instrument_pydantic_ai()`). La
recherche, le découpage et les embeddings portent des spans explicites : Logfire ne
voit pas ce code, qui n'est ni un appel HTTP ni un agent.

**Rien n'est envoyé sans token.** `send_to_logfire="if-token-present"` : sans
identifiants, le code tourne à l'identique et n'émet rien — c'est ce qui permet aux
tests de passer sans dépendre d'un compte.

Pour activer :

```bash
uv run logfire auth      # ouvre le navigateur, écrit .logfire/ (déjà dans .gitignore)
```

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
app/
  chat.py                   interface Streamlit
  api.py                    API REST FastAPI — mêmes réponses, autre transport
scripts/
  index.py                  construction de l'index vectoriel
  load_excel_to_db.py       classeur → tables teams / players / stats
  load_reports_to_db.py     PDF Reddit → table reports
src/
  config.py                 chemins, modèles, paramètres de découpage et de recherche
  schemas.py                les contrats Pydantic, aux frontières du système
  db_models.py              les quatre tables SQLAlchemy, déclarées STRICT
  loading/loaders.py        extraction du texte (PDF/OCR, Excel, CSV, DOCX, TXT)
  rag/vector_store.py       embeddings, index FAISS, recherche
  rag/sql_tool.py           exécution bridée du SQL écrit par le modèle
  rag/generation.py         contexte, prompt, agent, vérification des citations
  rag/pipeline.py           routage puis collecte puis réponse — le point d'entrée
eval/
  testset.json              les 18 cas
  evaluate_ragas.py         exécution du système + notation RAGAS
  analyze_results.ipynb     analyse des résultats
  results/                  un fichier JSON par run
tests/                      tests unitaires
notebooks/                  exploration des données sources
data/inputs/                PDF Reddit et classeur Excel
```

`src/rag/pipeline.py` est partagé par l'interface et par l'évaluation : le routage, le
prompt et l'appel au modèle n'existent qu'à un seul endroit. Sans cela, l'évaluation
pourrait mesurer autre chose que ce que l'application fait réellement.
