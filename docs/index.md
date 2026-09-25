# Rapport de mise en place et d'évaluation

**Assistant IA d'analyse de performance NBA — SportSee**
Septembre 2026

---

## Le problème, en une phrase

Le prototype d'assistant conversationnel de SportSee répondait bien aux questions
narratives et **inventait des chiffres** sur les questions statistiques, sans jamais le
signaler à l'utilisateur.

## Ce qui a été fait

Quatre couches ont été ajoutées au prototype, **chacune mesurée avant d'ajouter la
suivante**, sur un jeu de 24 questions représentatives.

| | Système évalué | `answer_correctness` |
|---|---|---|
| 1 | `baseline` — le prototype d'origine | 0.234 |
| 2 | `validation_pydantic` — sortie structurée et citations vérifiées | 0.296 |
| 3 | `tool_sql` — l'agent interroge la base sous quatre barrières | 0.391 |
| 4 | `guardrails` — validateur de couverture et cadrage des sources | **0.511** |

*Sur les 18 cas communs aux quatre runs.*

## Les trois résultats à retenir

**La justesse des réponses a plus que doublé** — `answer_correctness` de 0.234 à 0.511,
et la fidélité aux sources de 0.335 à 0.864.

**Le système ne répond plus aux questions dont la réponse n'existe pas.** Sur l'axe
comportemental — mesuré sans juge automatique, donc sans incertitude — il passe de
19/24 à **22/24**, et surtout **aucune question hors périmètre n'échappe plus à la
détection**. Les erreurs restantes sont des refus excessifs, c'est-à-dire le côté où
l'erreur est visible pour l'utilisateur.

**Le gain vient du code, pas du modèle.** La description de l'outil SQL avertissait
explicitement que les données ne contiennent aucune granularité par match. Trois
questions ont produit leur requête malgré cet avertissement. Ce qui a fonctionné, c'est
de **retirer l'outil** quand le schéma ne peut pas répondre — une décision prise en code,
hors du modèle.

## Ce qui reste ouvert

Deux familles de défaillance ne sont pas couvertes, et ce sont celles où **l'erreur est
invisible pour l'utilisateur** : une prémisse fausse dans la question, et une exhaustivité
que la recherche vectorielle ne peut pas garantir. Elles sont détaillées dans
[Limites et biais](limites.md), et les corrections proposées dans
[Conclusion et perspectives](conclusion.md).

---

## Sommaire

| Page | Ce qu'on y trouve |
|---|---|
| [Contexte et méthodologie](methodologie.md) | le diagnostic du prototype, les quatre couches et pourquoi elles ont été construites dans cet ordre |
| [Protocole d'évaluation](evaluation.md) | le jeu de test, les métriques, et pourquoi deux axes plutôt qu'un |
| [Résultats](resultats.md) | les chiffres, run par run et catégorie par catégorie |
| [Lecture métier](interpretation.md) | ce que chaque défaillance coûte à SportSee |
| [Limites et biais](limites.md) | ce que cette évaluation ne peut pas établir |
| [Conclusion et perspectives](conclusion.md) | ce que l'évaluation établit, et les six chantiers qui suivent |

Le code, l'architecture et la procédure de reproduction sont dans le
[README du dépôt](https://github.com/FaizaKh93/Projet10_mission). L'analyse détaillée,
cas par cas, vit dans le notebook `eval/analyze_results.ipynb`.
