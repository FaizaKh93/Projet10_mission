# 2. Méthodologie d'évaluation

Évaluer un système génératif pose un problème simple à énoncer et difficile à résoudre :
**il n'y a pas de bonne réponse unique**, donc pas de comparaison exacte possible. La
méthode retenue combine trois niveaux, du plus discutable au plus solide.

## 2.1 Le jeu de test

18 questions, dont la réponse attendue a été **vérifiée à la main dans les sources**.
Elles croisent deux axes, à six cas par valeur.

| | Reddit (PDF) | Excel | Hybride |
|---|---|---|---|
| **simple** | un fait à retrouver | une valeur à lire | identifier puis chiffrer |
| **complexe** | synthèse de points de vue | filtrage, classement, agrégation | candidats du texte, comparaison chiffrée |
| **bruitée** | information absente, question subjective | granularité ou dimension inexistante | conflit de présence entre sources |

Le croisement n'est pas décoratif. L'axe **modalité** dit *où* la réponse se trouve ;
l'axe **difficulté** dit *ce qu'il faut faire* de ce qu'on a trouvé. Un système peut
très bien récupérer le bon passage et mal raisonner dessus — et sans les deux axes, on
ne saurait pas lequel des deux a échoué.

!!! note "Les six cas bruités mesurent ce que les douze autres ne voient pas"
    Ils **n'ont pas de bonne réponse**. La seule conduite correcte est de signaler qu'on
    ne peut pas répondre. Un système qui réussit les douze premiers et invente sur les
    six derniers n'est pas utilisable : c'est précisément le comportement du prototype
    de départ.

Le jeu de test **n'a pas bougé** entre les quatre runs. C'est la condition pour que les
scores soient comparables — et cela a conduit à écarter une demande du cahier des
charges, celle d'étendre les cas de robustesse : les étendre pendant la comparaison
l'aurait invalidée. Les six cas hybrides existants ont donc été *analysés* plutôt que
complétés.

## 2.2 Les quatre métriques RAGAS

| Métrique | La question posée | Ce qu'elle juge |
|---|---|---|
| `faithfulness` | la réponse invente-t-elle des faits absents du contexte ? | la **génération** |
| `context_precision` | les fragments récupérés sont-ils pertinents ? | la **récupération** |
| `context_recall` | le contexte contient-il de quoi répondre ? | la **récupération** |
| `answer_correctness` | la réponse est-elle la bonne ? | le **résultat** |

Deux d'entre elles jugent la récupération, deux jugent la réponse. C'est ce découpage
qui permet de savoir **où** ça casse : une `faithfulness` élevée avec une
`context_precision` nulle décrit un modèle qui s'est bien comporté avec des extraits
inutiles — le défaut est dans la recherche, pas dans la génération.

### Comment la note est calculée

Trois des quatre métriques sont elles-mêmes des appels à un modèle. C'est pourquoi un
seul cas déclenche une vingtaine d'appels au juge.

| Métrique | Calcul |
|---|---|
| `faithfulness` | le juge découpe la réponse en **affirmations atomiques**, vérifie chacune contre le contexte, et rend le ratio des affirmations soutenues |
| `context_precision` | le juge marque chaque extrait « utile / inutile », puis calcule une précision moyenne **pondérée par le rang** — un bon extrait en première position vaut plus qu'en cinquième |
| `context_recall` | découpe la **réponse attendue** en affirmations et vérifie que chacune est attribuable au contexte |
| `answer_correctness` | **0,75 × F1 factuel + 0,25 × similarité sémantique** — le juge classe les affirmations en vrais positifs, faux positifs et faux négatifs |

## 2.3 Le juge, et sa faillibilité

Le système évalué est `mistral-small-latest`. Le juge est **`gpt-4o`**, délibérément
différent : un modèle qui se note lui-même se note bien. C'est la parade documentée
contre le biais d'auto-préférence.

Elle ne suffit pas. Le jugement par modèle souffre de biais connus — préférence pour les
réponses longues, sensibilité à la formulation, **non-déterminisme**. Deux exécutions
identiques ne rendent pas la même note.

### Le plancher de bruit, mesuré

Plutôt que de supposer ce bruit, il a été **quantifié**, à la faveur d'une circonstance
favorable : deux runs successifs — `reddit_only` et `pydantic_contracts` — partagent
**exactement les mêmes contextes récupérés, sur les 18 cas**. Seule la génération
changeait.

Leurs métriques de récupération auraient donc dû être identiques. Elles diffèrent de
**0.028**, l'écart tenant à un seul cas noté 0.833 puis 0.333.

!!! danger "Conséquence, appliquée dans tout ce rapport"
    **En dessous de 0.03 sur une moyenne, un écart n'est pas interprétable.** Il est
    dans le bruit du juge. Ce seuil est mesuré, pas estimé — et il a servi à écarter
    plusieurs « améliorations » apparentes.

## 2.4 Les contrôles sans juge

C'est le niveau le plus solide, et celui sur lequel reposent les conclusions de ce
rapport. Quatre décomptes ne dépendent d'aucun modèle :

| Contrôle | Comment | Pourquoi il est fiable |
|---|---|---|
| **Chiffres exacts** (/8) | recherche de sous-chaîne dans la réponse | le chiffre attendu a été vérifié à la main dans la source |
| **Routage correct** (/18) | la source choisie contre la modalité du cas | le jeu de test porte la source attendue |
| **Refus** (/6) | le champ `abstain` de la sortie structurée | déclaration explicite du système, plus une tournure à deviner |
| **Citations invalides** | confrontation au contexte réellement servi | vérifié en Python à chaque run |

Ces contrôles ont une propriété que les métriques RAGAS n'ont pas : ils sont
**reproductibles à l'identique**, et indépendants de la disponibilité du juge. C'est
sur eux que reposent les conclusions de ce rapport.

## 2.5 Ce que la méthode ne couvre pas

Trois réserves, énoncées ici pour ne pas être découvertes plus loin :

- **Un seul run par cas.** Le non-déterminisme du système évalué n'est pas mesuré, seul
  celui du juge l'est.
- **18 cas.** Un écart d'un cas pèse 5,6 points de pourcentage sur un décompte.
- **`answer_correctness` est une similarité, pas un taux de réussite.** Une abstention
  correcte ne ressemble pas à la réponse de référence, donc ne fait pas monter cette
  métrique. Le décompte des chiffres et celui des refus restent les mesures fiables.
