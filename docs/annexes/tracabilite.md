# Annexe — Traçabilité

Deux dispositifs se complètent : **Logfire** trace l'exécution pas à pas, et les
**champs enregistrés dans chaque réponse** rendent un résultat relisible après coup.

## Logfire

[Pydantic Logfire](https://pydantic.dev/logfire) instrumente les agents automatiquement.
Chaque question produit un arbre de spans :

```
cas  (id=S3)                        ← un span par question, pendant une évaluation
  agent run                         ← [1] le routage
    chat mistral-small…                 la source choisie, et son motif
  recherche_faiss                   ← [2] seulement si la route inclut les documents
  agent run                         ← [3] la génération
    chat mistral-small…
    running tool: interroger_base   ← la requête SQL écrite par le modèle
    chat mistral-small…             ← relance : citation refusée, ou requête refusée
```

Ce qu'on y voit et que les journaux ne donnaient pas :

- **la route effectivement prise**, et son motif ;
- **le SQL réellement exécuté** ;
- **la relance du validateur de citations** — invisible autrement, puisqu'elle se solde
  par une réponse corrigée ;
- le temps passé dans la recherche par rapport à la génération ;
- le contenu exact envoyé au modèle.

La recherche, le découpage et les embeddings portent des spans **explicites** : Logfire
ne voit pas ce code, qui n'est ni un appel HTTP ni un agent.

!!! note "Rien n'est envoyé sans token"
    `send_to_logfire="if-token-present"` : sans identifiants, le code tourne à
    l'identique et n'émet rien. C'est ce qui permet aux tests de passer sans dépendre
    d'un compte.

```bash
uv run logfire auth      # ouvre le navigateur, écrit .logfire/ (déjà dans .gitignore)
```

## Ce que chaque réponse conserve

La traçabilité ne dépend pas d'un service externe. Chaque réponse porte, dans sa
structure même :

| Champ | À quoi il sert |
|---|---|
| `route` | quelle source a été choisie |
| `route_motif` | ce qui a justifié ce choix, en une phrase |
| `requetes` | le SQL exécuté **et ses lignes** |
| `citations` | les identifiants invoqués, déjà vérifiés |
| `abstain` / `abstain_reason` | le refus, et sa raison |

C'est ce qui rend une réponse chiffrée **relisible sans être rejouée** — y compris
plusieurs mois plus tard, y compris par quelqu'un qui n'a pas écrit le code.

## La séparation exécution / notation

Le harnais d'évaluation enregistre ce que le système produit **avant** de le noter. Les
deux étapes n'ont ni le même coût ni le même fournisseur : le système répond via
Mistral, le juge note via OpenAI.

La conséquence est pratique : une indisponibilité du juge ne coûte que les scores. Les
réponses sont conservées avec un marqueur, et une commande les note a posteriori.

```bash
uv run python eval/evaluate_ragas.py --label sql_routing --renoter
```

Aucun appel Mistral : ni routage, ni recherche, ni requête SQL. Les cas déjà notés sont
ignorés, donc la commande se relance autant de fois que nécessaire.

!!! note "Pourquoi cette séparation compte"
    Une campagne d'évaluation mêle deux services facturés indépendants. Les traiter
    comme un tout unique revient à perdre le travail du premier chaque fois que le
    second est indisponible.
