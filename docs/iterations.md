# 4. Les quatre itérations

Une intervention, un run étiqueté. Chaque section dit **ce qui change**, **ce que la
mesure donne**, et **ce que ça a appris pour la suite**.

## 4.1 `baseline` — le prototype livré

**Ce qui change :** rien. C'est le point de départ, mesuré avant toute modification.

| `faithfulness` | `context_precision` | `context_recall` | `answer_correctness` |
|---|---|---|---|
| 0.408 | 0.165 | 0.435 | 0.196 |

**Chiffres exacts : 0/8. Refus : 1/6.**

`context_precision` a une **médiane de 0.000** : sur 14 cas sur 18, aucun des cinq
fragments récupérés n'est jugé pertinent. Découpé par modalité, le diagnostic est net —
**0.495 sur les questions Reddit, 0.000 sur les douze questions Excel et hybrides**. La
recherche sémantique tient sur du texte narratif et échoue entièrement dès qu'il faut
atteindre une valeur chiffrée.

L'examen de l'index explique pourquoi. La feuille « Données NBA », qui contient seule
les statistiques, pèse **143 des 302 fragments — 47 % de l'index — et n'est jamais
récupérée : 0 fois sur 90**. Sortent à sa place les feuilles qui *décrivent* les
données : le « Dictionnaire des données », 4 fragments, servi **25 fois**.

!!! info "Cohérent avec ce qu'est un embedding"
    « Combien de points Jokić a-t-il marqués ? » est sémantiquement proche d'une phrase
    qui *définit* le mot *points*, et loin d'une ligne de tableau aplatie où un nom
    voisine avec trente nombres sans étiquette.

**Ce que ça apprend :** ces 143 fragments sont du poids mort, et ils **nuisent** en
prenant aux fils Reddit des places dans le top-5. L'itération suivante le met à
l'épreuve.

## 4.2 `reddit_only` — le classeur sort de l'index

**Ce qui change :** `EXTENSIONS_INDEXEES` se limite aux PDF. L'index passe de 302 à 100
fragments.

| `faithfulness` | `context_precision` | `context_recall` | `answer_correctness` |
|---|---|---|---|
| 0.231 | 0.193 | 0.352 | 0.186 |

**Chiffres exacts : 0/8. Refus : 0/6.**

L'hypothèse est confirmée là où elle portait : sur les questions Reddit,
`context_precision` monte à **0.579** et `context_recall` à **0.722**. Les fils ne sont
plus concurrencés par des fragments de tableau.

Mais deux chiffres se dégradent, et c'était attendu :

- **`faithfulness` tombe à 0.231** — moins de sources disponibles, autant d'affirmations
  produites. Le modèle comble.
- **Les refus passent à 0/6.** Sur les cas bruités, `faithfulness` s'effondre à
  **0.036** : la quasi-totalité de ce que le système affirme est sans appui.

**Ce que ça apprend :** retirer une source ne suffit pas. Il faut aussi **encadrer la
sortie**, sans quoi le modèle remplace la donnée manquante par ce qu'il croit savoir.

## 4.3 `pydantic_contracts` — contrats et sortie structurée

**Ce qui change :** des modèles Pydantic à chaque frontière, une sortie structurée
(`answer`, `citations`, `abstain`, `abstain_reason`), et un validateur qui confronte
chaque citation au contexte réellement servi.

| `faithfulness` | `context_precision` | `context_recall` | `answer_correctness` |
|---|---|---|---|
| **0.784** | 0.165 | 0.324 | 0.268 |

**Chiffres exacts : 0/8. Refus : 3/6. Citations invalides : 0.**

C'est le mouvement le plus net des quatre runs. `faithfulness` passe de **0.231 à
0.784**, médiane à 0.883, et de **0.036 à 0.705 sur les cas bruités** — là où le
prototype inventait le plus.

`answer_correctness` monte de 0.186 à 0.268, et la **médiane** de 0.131 à 0.272 : la
distribution entière se déplace, ce n'est pas un cas isolé qui tire la moyenne.

Les métriques de récupération, elles, sont **inchangées** — la recherche n'a pas bougé.
C'est cette coïncidence qui a permis de mesurer le plancher de bruit du juge (§ 2.3).

**Ce que ça apprend :** l'abstention devient un **champ exploitable** au lieu d'une
tournure de phrase à deviner. Mais 6 des 9 abstentions portent sur les questions
chiffrées : le système a désormais raison de se taire, et le manque reste entier.

## 4.4 `sql_routing` — la base et le routage

**Ce qui change :** le classeur est chargé dans SQLite, un outil SQL bridé est offert au
modèle, et un premier appel choisit la source avant toute collecte.

| `faithfulness` | `context_precision` | `context_recall` | `answer_correctness` |
|---|---|---|---|
| 0.713 | 0.554 | 0.602 | **0.396** |

**Chiffres exacts : 5/8. Routage correct : 15/18. Refus : 2/6.**

!!! warning "Les deux métriques de contexte ne se comparent pas aux runs précédents"
    Leur bond vient d'un **changement d'unité**, pas d'une meilleure recherche. Le
    contrôle et la démonstration sont en [§ 6.1](analyse-critique.md).

Ce qui est réellement acquis : **5 chiffres exacts sur 8**, dont **4 sur 4** par requête
directe, et `answer_correctness` de 0.268 à **0.396** — plus de treize fois le plancher
de bruit.

Deux reculs, signalés : `faithfulness` de 0.784 à 0.713, et les refus de 3/6 à 2/6.

**Ce que ça apprend :** le chemin SQL direct fonctionne ; l'enchaînement documents → base
est le point faible ([§ 6.4](analyse-critique.md)).

Le détail par cas de chaque run est dans le [notebook d'analyse](notebook.ipynb), et la
comparaison des quatre en [section 5](comparatif.md).
