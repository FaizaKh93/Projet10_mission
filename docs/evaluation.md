# Protocole d'évaluation

## Le jeu de test

**24 questions**, construites à la main puis vérifiées une à une contre leur source. Aucune
n'a été générée automatiquement : chaque réponse de référence a été retrouvée dans le
classeur ou dans les fils Reddit.

Il est équilibré sur deux axes indépendants.

| Catégorie | Nombre | Ce qu'elle éprouve |
|---|---|---|
| simple | 8 | une information directement présente |
| complexe | 8 | filtrage, agrégation, comparaison, classement |
| bruitée | 8 | une question dont la réponse **n'existe pas**, ou pas sous la forme demandée |

| Modalité | Nombre | Source attendue |
|---|---|---|
| `excel` | 9 | la base statistique |
| `pdf_reddit` | 9 | les fils de discussion |
| `hybride` | 6 | **les deux** — le texte identifie l'entité, la base donne le chiffre |

Les six cas hybrides ont été ajoutés à la troisième étape. Ils sont donc absents des deux
premiers runs, et exclus de la comparaison longitudinale — qui porte sur les **18 cas
communs aux quatre runs**.

**Choix argumenté** — les cas bruités ne sont pas des questions ratées. Leur réponse de
référence est l'aveu d'une limite : *« non calculable : le fichier ne contient que des
agrégats de saison »*. Un score élevé signifie que le système a reconnu cette limite ; un
score bas, qu'il a répondu avec assurance à une question sans réponse fiable. **C'est le
comportement le plus coûteux en production**, et il fallait un tiers du jeu pour le
mesurer.

## Deux axes, délibérément

### Les quatre métriques RAGAS

Jugées par gpt-4o, un modèle **différent** de celui qui produit les réponses (Mistral) —
faire juger un modèle par lui-même introduirait une complaisance mesurable.

| Métrique | Question à laquelle elle répond |
|---|---|
| `faithfulness` | la réponse est-elle fondée sur le contexte fourni ? |
| `context_precision` | les fragments récupérés étaient-ils pertinents ? |
| `context_recall` | a-t-on récupéré tout ce qu'il fallait ? |
| `answer_correctness` | la réponse correspond-elle à la référence ? |

Les deux métriques de contexte sont affichées sous les noms `vector_context_*` dans
l'analyse : depuis l'ajout de l'outil SQL, elles ne jugent plus **tout** le contexte du
système, seulement sa branche vectorielle. Les renommer évite de les lire comme globales.

### L'axe comportemental, sans juge

`abstention_accuracy` croise l'indicateur `abstain` de la réponse avec le comportement
attendu déclaré dans le jeu de test. **Aucun modèle n'intervient** : c'est une comparaison
de deux booléens.

C'est le seul axe qui échappe à la variance du juge — et il mesure exactement ce qui
compte pour SportSee : *le système reconnaît-il qu'il ne peut pas répondre ?*

Quatre comportements attendus sont distingués, et la distinction est load-bearing :

| `expected_behavior` | Ce qu'on attend |
|---|---|
| `answer` | une réponse |
| `abstain_absence` | un refus : la donnée n'existe pas |
| `abstain_ambiguous` | un refus : la question n'a pas de réponse unique |
| `flag_anomaly` / `flag_limitation` | une **réponse qui signale** l'anomalie ou la limite |

Les deux derniers n'appellent pas d'abstention. Les compter comme tels pénaliserait à tort
un système qui fait ce qu'on lui demande.

## Le protocole d'exécution

Un run rejoue les 24 questions contre le système complet, enregistre chaque réponse avec
sa provenance, puis les fait juger. Le résultat s'écrit dans `eval/results/<label>.json`
et **n'écrase jamais** un run existant.

```bash
uv run python eval/evaluate_ragas.py --label <nom_du_run>
```

Quatre runs sont versionnés, un par couche. Le fichier conserve pour chaque cas : la
question, la réponse de référence, la réponse produite, les fragments récupérés, **les
requêtes SQL réellement exécutées**, les citations validées, l'indicateur d'abstention et
les quatre scores.

**Choix argumenté** — la fidélité est jugée sur un contexte **élargi** aux résultats SQL
pour les cas où l'outil a été appelé. Sans cela, une réponse parfaitement fondée sur une
requête aurait été jugée infidèle, faute de retrouver le chiffre dans les fragments
vectoriels. C'est l'information dont le système disposait réellement.

## Une précaution de lecture, mesurée

Le contexte récupéré est **identique** dans `baseline`, `tool_sql` et `guardrails` — la
recherche vectorielle n'a jamais été modifiée, vérifié sur les 18 cas. Pourtant
`context_recall` y vaut 0.528 contre 0.583.

Même entrée, score différent : c'est la **variance du juge**. Elle fixe un plancher
d'environ **±0.05** en dessous duquel un écart ne doit pas être interprété.

C'est une mesure, pas une estimation — et elle explique pourquoi l'axe comportemental,
qui n'en souffre pas, pèse autant dans les conclusions.
