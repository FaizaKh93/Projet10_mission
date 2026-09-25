# Lecture métier

## Ce que le produit doit faire

L'assistant de SportSee doit **animer le débat entre fans** à partir de données que
l'entreprise vend. Cette double nature commande tout : il n'est ni un moteur de recherche
— on attend de lui un point de vue — ni un simple agrégateur de commentaires — on attend
que ses chiffres soient justes.

Un chiffre inventé n'est donc pas une imprécision technique. C'est une atteinte au produit
à sa racine : **SportSee vend de la donnée, et son assistant en fabrique.**

## Le coût de chaque famille de défaillance

Les huit questions bruitées du jeu de test correspondent à quatre familles, chacune
adossée à un risque identifiable.

| Famille | Ce qu'un fan voit | Ce que ça coûte | État |
|---|---|---|---|
| **Donnée absente** | un chiffre précis, plausible, cité comme un fait | il sera repris dans une discussion, attribué à SportSee, et contredit par une source publique | **corrigé** |
| **Prémisse fausse** | une explication confiante d'une colonne corrompue | l'assistant valide une erreur du fichier source et la propage aux clients | **ouvert** |
| **Question ambiguë** | un verdict tranché sur un débat subjectif | l'assistant se substitue à la communauté au lieu de la nourrir — l'inverse du produit | **couvert** |
| **Exhaustivité impossible** | un classement présenté comme complet | établi sur cinq fragments d'un fil de quinze pages, indétectable par le lecteur | **ouvert** |

Les deux familles ouvertes sont celles où **l'erreur est invisible pour l'utilisateur** :
la réponse est fluide, plausible, et rien ne signale qu'elle est fabriquée. C'est le
risque le plus coûteux pour un produit dont l'argument est la fiabilité.

## Pourquoi le refus excessif est préférable

Le système commet aujourd'hui deux erreurs de comportement sur 24 cas, et ce sont **deux
refus excessifs**. Avant les garde-fous, il commettait quatre erreurs de l'autre sens :
répondre avec assurance à des questions sans réponse.

Cet échange est favorable, et pour une raison métier, pas technique.

Un refus est **visible**. Le fan voit qu'il n'a pas obtenu sa réponse, il reformule ou va
chercher ailleurs. La confiance n'est pas entamée — au contraire, un assistant qui dit
« je ne peux pas répondre à partir de mes données » se crédibilise.

Une réponse fabriquée est **invisible**. Elle circule, se cite, et ne se corrige que
lorsque quelqu'un la vérifie — c'est-à-dire trop tard, et publiquement.

> Pour un produit de contenu, **le coût d'un faux refus et celui d'une hallucination ne
> sont pas du même ordre.** Le premier se mesure en frustration, le second en crédibilité.

## Ce que les chiffres disent en termes produit

**La justesse a plus que doublé** — 0.234 à 0.511. Ce score est une **similarité
continue**, pas un taux de réussite : il ne se lit pas « une réponse juste sur quatre ».
Le décompte, lui, se lit : sur les 18 cas communs, **2 réponses dépassaient 0.5 avec le
prototype, 9 aujourd'hui**.

**Les questions analytiques sont celles qui progressent le plus** — +0.274 sur la
catégorie complexe grâce à l'outil SQL, +0.081 ensuite. Ce sont précisément les questions
que le prototype ne savait pas traiter, et celles qui différencient un assistant
statistique d'un moteur de recherche.

**Aucune question hors périmètre n'échappe plus à la détection.** C'est le seul chiffre du
rapport mesuré sans juge automatique, donc le plus solide.

## Le coût d'exploitation

Chaque question consomme désormais **un appel de modèle de plus** qu'auparavant : la
classification d'intention précède la génération, et la génération elle-même en consomme
plusieurs dès que l'outil SQL est sollicité.

Mesuré sur un appel réel via l'API :

```
recherche vectorielle   343 ms
génération             2270 ms
total                  2613 ms
```

L'appel supplémentaire utilise **le même modèle** que la génération — il n'y a pas de
hiérarchie de modèles dans ce système. Il reste nettement moins coûteux en jetons : son
prompt ne contient que la question, là où la génération y ajoute les cinq fragments
récupérés. Et il évite parfois une génération complète, puisqu'une question hors périmètre
produit une réponse plus courte.

Le surcoût réel n'a pas été chiffré à l'échelle : c'est une limite de cette évaluation.

## Ce que ça change pour l'utilisateur, concrètement

Trois comportements observables ont changé entre le prototype et le système actuel.

**Une question hors périmètre reçoit un refus argumenté**, et non plus un chiffre. *« Les
données ne contiennent aucun indicateur domicile/extérieur »* plutôt qu'une comparaison
fabriquée.

**Une question analytique reçoit une réponse calculée sur l'ensemble des données**, et non
plus déduite de cinq fragments — avec la requête exécutée consultable, donc vérifiable.

**Une question d'opinion reste une question d'opinion.** Le système rapporte ce que les
fans disent au lieu de trancher, ce qui est le comportement attendu d'un produit dont la
mission est d'animer le débat.
