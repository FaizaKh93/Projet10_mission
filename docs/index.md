# Assistant NBA — rapport de mise en place et d'évaluation

Un prototype d'assistant répond à des questions de fans de NBA à partir de deux
sources : quatre fils de discussion Reddit et un classeur de statistiques de saison
régulière. Ce rapport rend compte de son **audit**, de son **extension** et de son
**évaluation**.

## Le résultat en une ligne

!!! success "Le manque central est comblé"
    **5 réponses chiffrées exactes sur 8**, contre **0 sur 8** au départ. Sur les
    questions qui tiennent en une requête directe : **4 sur 4**.

## Quatre itérations, quatre mesures

Chaque intervention a été mesurée séparément, sur le même jeu de 18 questions, avec le
même juge. C'est ce qui permet d'attribuer un effet à une cause.

| Run | Ce qui change | `answer_correctness` | Chiffres exacts |
|---|---|---|---|
| `baseline` | rien — le prototype livré | 0.196 | 0 / 8 |
| `reddit_only` | le classeur sort de l'index vectoriel | 0.186 | 0 / 8 |
| `pydantic_contracts` | contrats d'entrée et de sortie, citations vérifiées | 0.268 | 0 / 8 |
| `sql_routing` | base relationnelle et routage de la question | **0.396** | **5 / 8** |

## Ce que ce rapport défend

**Le modèle propose, le code dispose.** Le routeur choisit la source mais ne collecte
pas ; le modèle écrit le SQL mais ne l'exécute pas ; il cite ses sources mais ses
citations sont vérifiées en Python. Chaque fois qu'une consigne dans un prompt aurait
pu suffire, elle a été remplacée par une contrainte appliquée par le code.

**Une mesure vaut mieux qu'une intuition.** Le plancher de bruit du juge est mesuré, pas
supposé : 0.028. En dessous, un écart n'est pas interprétable — et deux des gains
apparents de la dernière itération sont écartés pour cette raison.

**Les limites sont nommées.** Le chemin hybride reste faible (1 sur 4), les refus
reculent, et deux défauts sont documentés **sans être corrigés** : les corriger sans
mesurer n'aurait rien prouvé.

## Comment lire ce rapport

| Vous voulez | Allez à |
|---|---|
| comprendre le besoin et le point de départ | [1. Contexte et objectifs](contexte.md) |
| savoir comment la fiabilité est mesurée | [2. Méthodologie](methodologie.md) |
| comprendre l'architecture | [3. Le système](systeme/vue-ensemble.md) |
| voir les résultats et leur évolution | [5. Comparatif](comparatif.md) |
| connaître les limites et les suites | [6. Analyse critique](analyse-critique.md) · [7. Conclusion et perspectives](conclusion.md) |

Le code, l'installation et les commandes sont dans le
[README du dépôt](https://github.com/FaizaKh93/Projet10_mission). Ce rapport ne les
répète pas : il explique les choix et rend compte des mesures.
