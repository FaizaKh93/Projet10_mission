# 3.4 Le choix de la source

Un premier appel au modèle choisit la source **avant toute collecte**, et rend sa
décision *et son motif*. Une route est donc vérifiable après coup — c'est ce qui a permis
de la noter sans aucun juge.

| Route | Quand | Ce qui est collecté |
|---|---|---|
| `documents` | opinions, débats, commentaires | recherche FAISS |
| `base` | chiffres, totaux, classements | rien — l'outil SQL suffit |
| `les_deux` | identifier quelqu'un *puis* le chiffrer | recherche FAISS **et** outil SQL |
| `aucune` | hors des deux sources | rien |

## Ce que le routeur voit

Sa consigne fait environ **1 000 caractères**, construits à chaque appel à partir du
profil réel de la base. Elle ne contient **aucun nom de colonne**.

```
1. Les documents : quatre fils de discussion Reddit. Opinions, débats,
   jugements de fans. Aucune statistique fiable.

2. La base de données.
La base contient les statistiques agrégées de 569 joueurs sur une saison
régulière, réparties en 3 tables (teams, players, stats).

Elle ne permet pas de répondre aux questions suivantes :
- aucune granularité par match : pas de date, pas d'adversaire, pas de
  distinction domicile/extérieur
- une seule saison en base : aucune comparaison ni évolution entre saisons
- ...
```

!!! abstract "Le routeur décide sur les limites, jamais sur les capacités"
    C'est délibéré. Lui énoncer 47 noms de colonnes ferait basculer vers `base` toute
    question contenant un mot statistique — et **aggraverait** précisément le biais
    constaté ([§ 6.2](../analyse-critique.md)).

    Ce qui le fait travailler, ce sont les **absences**. Sur une question de comparaison
    domicile/extérieur, son motif cite la limite qu'il vient de lire : *« La base ne
    permet pas de distinguer les statistiques par lieu. »* Il choisit quand même
    `base` — comme la consigne le lui demande, pour que l'absence soit **constatée là où
    elle existe**.

Ces limites sont dérivées par introspection, jamais recopiées. Le jour où la donnée par
match existera, la ligne disparaîtra seule.

## Retirer plutôt que déconseiller

Quand la route exclut la base, **l'outil SQL n'est pas déconseillé au modèle : il lui
est retiré** de la liste des outils transmise.

C'est le principe directeur du système, appliqué ici. Une consigne dans un prompt ne
contraint rien — l'absence de l'outil, si.

Symétriquement, une route `base` n'engage **aucun embedding facturé** pour un contexte
qui ne servirait pas. Le routage n'est pas qu'une orientation, c'est aussi une économie.

## Ce que ça donne

**15 routages corrects sur 18**, mesurés sans juge : le jeu de test porte la source
attendue dans chaque cas.

| Modalité | Route attendue | Observé |
|---|---|---|
| `pdf_reddit` (6 cas) | `documents` | **6 / 6** |
| `excel` (6 cas) | `base` | **6 / 6** |
| `hybride` (6 cas) | `les_deux` | **3 / 6** |

Les deux modalités pures sont parfaites. **Les trois erreurs sont toutes des hybrides**
envoyés vers la base seule, et elles recoupent exactement les trois questions chiffrées
manquées. L'analyse de ce biais est en [§ 6.2](../analyse-critique.md).

## Trois limites assumées

**Décider avant de chercher, c'est décider sans savoir ce qu'on trouvera.** Le routeur
ne peut pas garantir que l'information existe dans la source qu'il désigne :
l'abstention reste la responsabilité du nœud de réponse.

**Une panne du routeur replie sur `les_deux`.** Une collecte de plus, mais aucune source
perdue — et le motif enregistré porte alors la mention de l'indisponibilité, donc
la panne reste lisible après coup.

**La recherche documentaire n'est pas rejouable.** Elle part avec la question brute,
avant toute collecte : impossible de chercher dans les documents *après* avoir obtenu un
résultat SQL. Aucun cas du jeu de test ne l'exige, mais la limite est réelle
([§ 6.5](../analyse-critique.md)).
