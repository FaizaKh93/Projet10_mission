# Conclusion et perspectives

## Ce que l'évaluation établit

Le prototype de SportSee ne souffrait pas d'un défaut de réglage. Il souffrait d'une
**inadéquation entre la question posée et le mécanisme chargé d'y répondre** : une
recherche par similarité sémantique ne sait ni filtrer ni agréger, et un modèle sommé de
produire une réponse en produit une.

Quatre couches ont porté la justesse de 0.234 à 0.511 et la fidélité de 0.335 à 0.864. Mais
le résultat qui compte n'est pas là — il est dans l'axe mesuré **sans juge automatique** :

> Le système ne répond plus aux questions dont la réponse n'existe pas.
> **19/24 → 22/24**, et plus aucune question hors périmètre non détectée.

Et la leçon transversale, vérifiée trois fois plutôt qu'affirmée une :

> **Une consigne dans le prompt ne contraint rien.**
> La description de l'outil avertissait explicitement de l'absence de granularité par
> match. Trois questions ont produit leur requête quand même. Ce qui a fonctionné, c'est
> de **retirer l'outil**.

Ce déplacement — du prompt vers le code — est ce qui distingue ce système du prototype. Le
modèle décrit une intention ; une règle déterministe décide. La règle est testable,
reproductible, et son erreur est traçable.

## Ce que l'évaluation n'établit pas

Une seule exécution par système, un seul juge, et un jeu de test qui a servi au réglage.
Les écarts inférieurs à ±0.05 ne s'interprètent pas — c'est la variance mesurée du juge.

Deux familles de défaillance restent ouvertes, et ce sont celles où **l'erreur est
invisible** : une prémisse fausse dans la question, et une exhaustivité que la recherche
ne peut pas garantir. Le système continue d'y répondre avec assurance.

## Perspectives

Six chantiers, classés par rapport entre le coût et ce qu'ils débloquent.

### 1. Un jeu de test tenu à l'écart — *quelques jours, aucun prérequis*

**Le problème** : les 24 questions ont servi à régler le prompt de classification et le
registre. Le score qu'elles produisent majore la performance réelle.

**L'action** : constituer 30 à 50 questions supplémentaires, **jamais consultées pendant
le développement**, incluant paraphrases, fautes de frappe et questions hors domaine.
Mesurer une fois, à la fin.

**Ce que ça débloque** : la seule façon de savoir si le système généralise. Tout le reste
des conclusions en dépend.

### 2. Se prémunir de l'injection de prompt — *quelques jours, à faire avant toute mise en ligne*

**Le problème** : le corpus est du texte d'utilisateurs Reddit, injecté tel quel dans le
prompt. Rien n'empêche un commentaire de contenir des instructions.

**L'action** : délimiter explicitement le contenu récupéré dans le prompt, tester un jeu
de commentaires adverses, et mesurer si l'agent peut être détourné.

**Ce que ça débloque** : c'est un prérequis de mise en production, pas une amélioration.

### 3. Vérifier les entités avant de requêter — *une à deux semaines*

**Le problème** : une erreur d'identification en amont produit une requête techniquement
correcte sur la mauvaise entité. Elle se propage sans aucun signal — deux cas observés.

**L'action** : exiger qu'une entité envoyée au SQL soit justifiée par un fragment cité.
Le mécanisme existe déjà pour les citations (`verify_citations`), il s'étend aux entités.

**Ce que ça débloque** : la fiabilité des questions hybrides, qui sont les plus
différenciantes du produit — croiser une discussion et une statistique.

### 4. Reclasser les résultats de recherche — *une à deux semaines*

**Le problème** : la récupération prend les cinq plus proches, sans seuil de pertinence.
Une reformulation anodine suffit à faire sortir le bon fragment des cinq premiers.

**L'action** : ajouter un *cross-encoder* de reclassement, dont le score est calibré pour
la pertinence — contrairement à la similarité cosinus. Puis seuiller sur ce score.

**Ce que ça débloque** : la stabilité des réponses face aux reformulations, et la
possibilité d'un court-circuit fiable quand aucun fragment n'est pertinent.

### 5. Étendre le périmètre des données — *dépend d'une source externe*

**Le problème** : une saison, saison régulière, aucune granularité par match. Une part des
questions légitimes des fans est **intrinsèquement sans réponse** — les playoffs, le
domicile/extérieur, les tendances récentes.

**L'action** : intégrer une seconde source — calendrier et *box scores* — et étendre le
registre de capacités en conséquence.

**Ce que ça débloque** : ce n'est plus de la fiabilité, c'est de la couverture. Le
validateur transforme aujourd'hui ces questions en refus ; cette action les transforme en
réponses.

### 6. Observer en continu ce que le garde-fou refuse — *quelques jours*

**Le problème** : le registre ne protège que des dimensions qu'on a pensé à inscrire. Une
dimension imprévue passe exactement comme avant.

**L'action** : exploiter les journaux — chaque refus est déjà tracé avec son motif et
l'intention extraite. Les agréger permet de découvrir les dimensions manquantes **à partir
des questions réellement posées**, au lieu de les deviner.

**Ce que ça débloque** : un mécanisme qui s'améliore avec l'usage, au lieu de figer les
hypothèses de conception.

## En une phrase

Le système actuel est **fiable sur son périmètre et honnête sur ses limites** — ce qui
était l'objectif. Il reste deux angles morts où il se trompe sans le dire, et un jeu de
test qui ne permet pas encore d'affirmer qu'il généralise. Ces trois points sont les
premiers de la liste ci-dessus.
