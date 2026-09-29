# 3. Le système

Deux sources, traitées de façon **entièrement différente**, et un aiguillage entre les
deux. C'est la clé pour lire le reste du dépôt.

| | Quatre fils Reddit | Un classeur de statistiques |
|---|---|---|
| Nature | opinions, débats, jugements de fans | 569 joueurs, saison régulière |
| Préparation | OCR, découpage, vectorisation | lecture, validation, insertion |
| Stockage | index FAISS, 100 fragments | SQLite, 4 tables |
| Interrogation | recherche sémantique, top-5 | SQL écrit par le modèle |
| Sait répondre à | « pourquoi les fans trouvent-ils… » | « combien de points… » |

Cette asymétrie n'est pas un accident de conception : elle vient d'un constat mesuré.
Une recherche sémantique fonctionne sur du texte narratif et **échoue entièrement** dès
qu'il faut atteindre une valeur chiffrée — au point que la feuille de statistiques,
47 % de l'index de départ, n'était jamais récupérée
([§ 4.1](../iterations.md)).

## Ce que contient cette section

| | |
|---|---|
| [3.1 Vue d'ensemble](vue-ensemble.md) | l'architecture, le principe directeur, les contrats |
| [3.2 Les discussions Reddit](reddit.md) | de la capture d'écran à l'index vectoriel |
| [3.3 Le classeur de statistiques](classeur.md) | du tableur à la base, et l'outil SQL bridé |
| [3.4 Le choix de la source](routage.md) | comment l'aiguillage décide, et ce qu'il rate |

Un principe traverse les quatre pages — **le modèle propose, le code dispose** — et il
est développé en [3.1](vue-ensemble.md).
