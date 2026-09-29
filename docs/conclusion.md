# 7. Conclusion et perspectives

## 7.1 Ce qui est acquis

**Le manque central est comblé.** Les questions chiffrées passent de **0 sur 8 à 5 sur
8**, et de 0 à **4 sur 4** quand elles tiennent en une requête directe. C'était le seul
défaut que ni les contrats ni la restriction de l'index n'avaient déplacé.

**Une absence est devenue démontrable.** Interrogé sur un joueur qui n'existe pas dans
les données, le système fabriquait des chiffres — et au troisième run, **en citant des
fragments parfaitement réels**. Au quatrième, la requête rend zéro ligne et il s'abstient
avec un motif exact.

!!! quote "Le résultat le moins attendu du projet"
    Une requête sans résultat est une **preuve d'absence**. Aucun corpus documentaire ne
    peut en fournir : un corpus muet sur un sujet est indiscernable d'une recherche
    ratée. C'est une propriété de la source structurée, pas du modèle.

**Le système sait où il regarde, et le dit.** Route, motif et requêtes exécutées sont
enregistrés à chaque appel. Une réponse chiffrée se relit sans être rejouée — y compris
plusieurs mois après, y compris par quelqu'un qui n'a pas écrit le code.

**Le principe a tenu.** *Le modèle propose, le code dispose* : le routeur choisit sans
collecter, le modèle écrit le SQL sans l'exécuter, il cite sans qu'on le croie. À chaque
fois qu'une consigne dans un prompt aurait pu suffire, elle a été remplacée par une
contrainte appliquée par le code — et la mesure a montré pourquoi (§ 6.3).

## 7.2 Ce qui ne l'est pas

| Limite | Mesure |
|---|---|
| Le chemin hybride | **1 sur 4** — identifier puis chiffrer casse |
| Les refus sur questions sans réponse | **3/6 → 2/6**, en recul |
| `faithfulness` | 0.784 → 0.713, au-dessus du plancher de bruit |
| Deux défauts documentés, non corrigés | une dimension fabriquée en SQL, une colonne confondue |

Ce recul des refus est le **prix assumé** de l'accès aux données : là où le système se
taisait faute d'accès, il interroge et répond à côté. Le gain sur les chiffres (+5) est
supérieur à la perte sur les refus (−1), mais la perte est réelle et elle est signalée.

## 7.3 Perspectives de mise en production

Le système répond correctement et se laisse auditer. Il n'est pas pour autant
déployable en l'état : ce qui suit distingue ce qu'il faut traiter **avant une mise en
service** de ce qui relève du **passage à l'échelle**.

### Moyen terme — avant une mise en service

**Protéger l'accès.** L'API n'a aujourd'hui **aucune authentification**, et `/logs`
expose questions et réponses à qui les demande. Trois mesures, par ordre d'urgence :
restreindre ou retirer `/logs`, poser une authentification par clé, et plafonner le
nombre de requêtes par client — chaque appel coûte de l'argent chez deux fournisseurs.

**Rendre le journal durable.** Il est volatile, borné à 100 entrées et propre à chaque
worker. En production il doit survivre à un redémarrage et être commun aux instances.
Ce n'est pas qu'une question de confort : c'est ce qui permet de mesurer **en continu**
ce que l'évaluation ne mesure aujourd'hui que ponctuellement.

**Surveiller les trois indicateurs sans juge.** Le projet a montré que le taux
d'abstention, l'exactitude du routage et la présence de requêtes SQL se mesurent
**sans aucun coût et sans aucun modèle**. Ce sont les alertes naturelles d'une mise en
production : une chute du taux de requêtes SQL signale une régression du routage bien
avant qu'un utilisateur ne s'en plaigne.

**Maîtriser le coût par requête.** Le routage économise déjà un embedding sur les
questions purement chiffrées. Il reste à mesurer le coût unitaire réel, et à décider si
un cache sur les questions fréquentes se justifie.

**Passer la suite gratuite en intégration continue.** 177 tests, aucune clé API
requise : ils peuvent tourner à chaque modification. La suite payante, jamais
automatiquement.

### Long terme — passage à l'échelle

**Épingler la version du modèle.** `mistral-small-latest` est un **alias mouvant**. Le
fournisseur le met à jour, et les réponses changent sans qu'une ligne de code ait bougé
— tous les résultats de ce rapport deviendraient alors incomparables. En production, la
version doit être fixée, et toute montée précédée d'une réévaluation.

**Actualiser les données.** La base contient une saison figée. Un déploiement réel
suppose une ingestion incrémentale, et surtout un **versionnement conjoint de l'index et
de la base** : sans lui, on ne peut pas rejouer une réponse rendue trois mois plus tôt,
et la traçabilité construite ici perd son intérêt.

**Changer de base quand elle grandit.** SQLite convient à 569 lignes en lecture seule.
Plusieurs saisons, plusieurs clubs et des écritures concurrentes appellent PostgreSQL.
Trois des quatre barrières se transposent directement — rôle en lecture seule, délai
d'exécution, bornes de taille. **L'autoriseur SQLite, lui, n'a pas d'équivalent direct**
et devra être repensé, probablement en vues restreintes et permissions par rôle. C'est
le point technique à instruire en premier.

**Transposer à un autre club.** Le profil de capacités étant **dérivé par
introspection**, il suit automatiquement un nouveau schéma : c'était la raison de ce
choix. Ce qui ne suit pas, c'est le **jeu de test** — 18 questions dont les réponses ont
été vérifiées à la main dans ce corpus précis. C'est lui le vrai frein à la
transposition, et c'est donc sa constitution qu'il faut industrialiser.

**Évaluer en continu plutôt qu'en campagne.** Quatre runs manuels ont suffi à ce
projet. Un système en service demande une évaluation déclenchée à chaque changement de
modèle, de prompt ou de données, avec le **plancher de bruit de 0.028 comme seuil
d'alerte** — il est déjà mesuré, il ne reste qu'à l'exploiter.

### Les limites fonctionnelles, à trancher avec le métier

Les trois points suivants ne sont pas des choix techniques mais des arbitrages produit,
et ils méritent d'être posés avant tout développement :

| Limite | Question à trancher |
|---|---|
| Chemin hybride à 1/4 | accepte-t-on ce taux, ou faut-il rendre la recherche documentaire rappelable après le SQL ? |
| Refus à 2/6 | préfère-t-on un système qui se tait plus souvent, quitte à refuser des questions légitimes ? |
| Pas de granularité par match | faut-il acquérir cette donnée, ou assumer que la question restera sans réponse ? |

## 7.4 Ce que ce projet aura surtout montré

Trois constats, transposables au-delà de ce corpus :

**Une consigne dans un prompt ne contraint rien.** Le même fait, écrit deux fois dans la
description d'un outil, a été ignoré. Ce qui a fonctionné, systématiquement, c'est de
retirer la possibilité plutôt que de la déconseiller.

**Une métrique peut progresser sans que rien ne s'améliore.** Le bond de 0.39 sur
`context_precision` était entièrement définitionnel. Sans un contrôle — les questions
Reddit, dont le mécanisme n'avait pas bougé — il aurait été présenté comme le meilleur
résultat du projet.

**Mesurer le bruit vaut mieux que le supposer.** Le plancher de 0.028 n'est pas une
convention : il vient de deux runs aux contextes rigoureusement identiques. Il a servi à
écarter des gains apparents autant qu'à valider les vrais.
