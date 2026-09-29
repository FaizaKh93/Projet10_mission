# 6. Analyse critique

Cette section rassemble ce qui ne marche pas, ce qui marche pour de mauvaises raisons,
et ce qu'on a choisi de ne pas corriger.

## 6.1 Un gain qui n'en est pas un

C'est le point de méthode le plus important du rapport.

Entre le troisième et le quatrième run, `context_precision` passe de **0.165 à 0.554**
et `context_recall` de **0.324 à 0.602**. Un bond de 0.39 sur une métrique dont le
plancher de bruit est 0.028 : treize fois le seuil d'interprétabilité.

**Ce gain ne doit rien à une meilleure recherche.**

Il vient d'un changement d'**unité**. Auparavant, une question chiffrée recevait cinq
extraits Reddit — jugés, à juste titre, totalement hors sujet : `context_precision`
valait **0.000** sur ces six cas, dans les trois premiers runs. Désormais elle reçoit
**une ligne de base exacte**, que le juge trouve évidemment pertinente.

Le contrôle est net, et il tient en une ligne :

| Sur les questions **Reddit** | `context_precision` | `context_recall` |
|---|---|---|
| `pydantic_contracts` | 0.495 | 0.639 |
| `sql_routing` | **0.495** | **0.639** |

Identiques au centième. Là où le mécanisme de récupération n'a pas changé, les métriques
n'ont pas bougé — exactement ce qu'on attend d'un contrôle. **Tout le gain global vient
des six questions Excel**, et il est définitionnel.

!!! danger "Ce qu'il faut en retenir"
    Ces deux métriques **ne se comparent pas** entre le troisième et le quatrième run.
    Présenter le passage de 0.165 à 0.554 comme une amélioration de la recherche serait
    faux. La comparaison honnête porte sur `answer_correctness`, qui compare la réponse
    à la référence sans dépendre de l'unité récupérée : **0.268 → 0.396**.

## 6.2 Le biais du mapping langage naturel → SQL

Le routage est correct **15 fois sur 18**. Les trois erreurs ne sont pas dispersées :
ce sont toutes des questions **hybrides envoyées vers la base seule**.

Leur point commun est visible à l'œil nu : elles sont formulées comme une demande de
chiffre pur — « combien de passes décisives… », « combien de points… » — et l'étape
d'identification dans les documents y est **implicite**.

Le routeur le dit lui-même. Sur la question du trash-talker des Pacers, son motif est :

> *« La question demande un chiffre précis (nombre de passes décisives) qui ne peut être
> obtenu que via la base de données. »*

Il a vu le chiffre. Il n'a pas vu qu'il fallait d'abord savoir **de qui** on parle.

### Pourquoi ce n'est pas un manque d'information

Le routeur décide sur les **limites** de la base — ce qu'elle ne sait pas faire — et
jamais sur ses colonnes. Ce choix est délibéré : lui énoncer 47 noms de colonnes ferait
basculer vers `base` toute question contenant un mot statistique, et **aggraverait
exactement ce biais**.

L'angle mort n'est donc pas du côté de la base, mais du côté des **documents** : rien
n'attire l'attention du routeur sur le fait qu'une question peut exiger une
identification préalable.

## 6.3 Deux défauts identifiés et non corrigés

Les refus sur les questions sans réponse reculent de **3/6 à 2/6**. Là où le système se
taisait faute d'accès aux données, il interroge la base et répond à côté.

Deux cas méritent d'être exposés, parce qu'**aucun des deux n'invente hors des sources** :

=== "B4 — la dimension fabriquée"

    On demande une comparaison domicile / extérieur. Cette dimension **n'existe pas**
    dans les données. Le modèle écrit :

    ```sql
    SELECT CASE WHEN s.team_code IN ('ATL','BKN','BOS',...) THEN 'Domicile'
                ELSE 'Extérieur' END, ...
    ```

    Il répartit arbitrairement les équipes en deux paquets — 547 joueurs contre 22 — et
    présente le résultat comme une comparaison domicile/extérieur. Il **a** utilisé la
    source ; il a fabriqué la dimension.

=== "B6 — la colonne confondue"

    On demande les points marqués lors d'une série de playoffs. Le modèle écrit :

    ```sql
    SELECT MAX(s.wins_total) AS total_points FROM stats s ...
    ```

    Il interroge les **victoires**, les renomme `total_points`, et annonce « 49 points
    marqués en playoffs ». L'alias masque la vraie colonne : `SQLResult.colonnes`
    contient `total_points`, pas `wins_total`.

### Pourquoi ils n'ont pas été corrigés

La réponse instinctive serait d'ajouter une consigne : *« n'invente pas une dimension
absente »*, ou *« réponds exclusivement depuis les sources »*.

**La description transmise à l'outil énonce déjà deux fois** qu'il n'y a pas de
distinction domicile/extérieur :

```
- aucune granularité par match : pas de date, pas d'adversaire, pas de distinction domicile/extérieur
- pas de filtre domicile/extérieur
```

Le modèle a écrit son `CASE WHEN` en ayant cela sous les yeux. **Le défaut n'est pas un
manque d'information : c'est qu'une information ne contraint pas.** Ajouter une
troisième formulation de la même chose serait un correctif non mesuré, et contredirait
le principe qui tient tout le reste du système — *le modèle propose, le code dispose*.

Aucun garde-fou applicable en code n'a été trouvé pour ces deux cas :

- détecter un `CASE WHEN` qui fabrique une dimension interdirait aussi des requêtes
  légitimes ;
- détecter l'alias trompeur de B6 demanderait d'analyser le SQL, pas seulement son
  résultat.

Ils sont donc **documentés comme limites ouvertes**, ce qui est plus honnête qu'un
correctif dont on ne saurait pas dire s'il fonctionne.

## 6.4 Le chemin hybride reste le point faible

Sur les huit questions chiffrées :

| Modalité | Réussite | Ce qu'il faut faire |
|---|---|---|
| **Excel** | **4 / 4** | une requête directe |
| **Hybride** | **1 / 4** | identifier dans les documents, *puis* chiffrer |

L'enchaînement casse de deux façons distinctes :

- **deux cas mal routés** — partis vers la base seule, donc sans le nom à chercher ;
- **un cas correctement routé** vers les deux sources, mais qui **n'a exécuté aucune
  requête**. Le modèle a répondu depuis les seuls extraits, sans jamais appeler l'outil
  qui lui était offert.

Ce second cas est le plus instructif : **offrir un outil ne garantit pas son usage**.
C'est la limite symétrique du principe qui a bien fonctionné ailleurs — retirer un outil
empêche son usage de façon certaine, l'offrir ne l'impose pas.

## 6.5 Une limite d'architecture

La route « les deux sources » lance la recherche vectorielle **avec la question brute**,
avant toute collecte. Le système ne peut donc pas relancer une recherche documentaire
*après* avoir obtenu un résultat SQL.

Une question comme « que disent les discussions des trois joueurs au meilleur TS% ? »
exigerait cet ordre inverse. Aucun cas du jeu de test ne le demande — les quatre cas
hybrides répondables vont tous dans le sens documents → base — mais la limite est réelle
et sera rencontrée dès qu'un corpus plus large sera branché.

## 6.6 Réserves sur la mesure elle-même

| Réserve | Portée |
|---|---|
| Un seul run par cas | le non-déterminisme du système évalué n'est pas mesuré |
| 18 cas | un cas pèse 5,6 points sur un décompte |
| `context_recall` sur les cas bruités | faussée : la référence y énonce une absence, qu'un contexte vide « soutient » |
| `answer_correctness` | une similarité, pas un taux de réussite |
| Le juge | non déterministe ; plancher mesuré à 0.028 |
