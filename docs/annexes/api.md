# Annexe — L'API REST

Même pipeline que l'interface web, autre transport : les deux appellent `repondre()`.
Une réponse servie en HTTP est donc celle que l'évaluation mesure.

```bash
uv run uvicorn app.api:app --reload
```

| Endpoint | Rôle |
|---|---|
| `GET /` | identité du service et liste des endpoints |
| `GET /health` | *liveness* — le processus répond, aucune dépendance vérifiée |
| `GET /ready` | *readiness* — index, base et clé API ; **503** si l'une manque |
| `POST /ask` | poser une question |
| `GET /logs` | les 100 derniers appels |

La documentation interactive complète est sur **`/docs`**, générée par FastAPI depuis
les modèles Pydantic : les schémas ne sont écrits qu'une fois, dans le code.

## Poser une question

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Combien de points Nikola Jokić a-t-il marqués cette saison ?"}'
```

```json
{
  "reponse": {
    "answer": "Nikola Jokić a marqué un total de 2 072 points cette saison.",
    "citations": ["sql_1"],
    "abstain": false,
    "abstain_reason": null
  },
  "route": "base",
  "route_motif": "La question demande un chiffre total de points marqués par un joueur sur une saison, ce que la base de données peut fournir.",
  "requetes": [
    {
      "requete": "SELECT s.pts_total FROM stats s JOIN players p ON p.player_id = s.player_id WHERE p.full_name = 'Nikola Jokić'",
      "colonnes": ["pts_total"],
      "lignes": [[2072]],
      "tronque": false
    }
  ],
  "latence_ms": {"total": 1412.6}
}
```

!!! abstract "`route` et `requetes` ne sont pas décoratifs"
    Sans eux, l'appelant devrait croire le modèle sur parole. Avec eux, il rejoue la
    requête et vérifie le chiffre.

La décomposition fine de la latence n'est pas dans la réponse : `repondre()` enchaîne
routage, collecte et génération en un seul appel, et l'API ne peut pas le découper sans
rejouer la logique du pipeline. Elle vit dans la trace Logfire, qui ouvre un span par
étape ([annexe traçabilité](tracabilite.md)).

## Quand la réponse n'existe pas

```json
{
  "reponse": {
    "answer": "Je n'ai pas pu trouver Reggie Miller dans la base de données des joueurs.",
    "citations": [],
    "abstain": true,
    "abstain_reason": "Reggie Miller n'apparaît pas dans la base de données des joueurs pour la saison disponible."
  },
  "route": "base",
  "requetes": [{"requete": "SELECT s.pts_total FROM stats s JOIN players p ...", "lignes": []}]
}
```

**Une abstention est un succès : HTTP 200.** Le traitement est allé au bout et a conclu
qu'il ne pouvait pas répondre. La compter comme une panne fausserait toute lecture des
journaux — et c'est la distinction que tout le projet défend.

La requête à zéro ligne est ce qui **prouve** l'absence.

## Codes de retour

| Code | Sur `/ask` |
|---|---|
| `200` | traitement abouti — **y compris une abstention** |
| `422` | question vide, trop courte ou de plus de 500 caractères — rejetée par Pydantic **avant** tout appel facturé |
| `503` | index vectoriel indisponible |
| `500` | panne pendant le traitement — journalisée avec son type d'exception |

## Le journal

`/logs` rend les 100 derniers appels, du plus récent au plus ancien, avec pour chacun :
la route et son motif, les requêtes SQL exécutées, les citations, l'abstention et son
motif, la latence, et l'identifiant de trace Logfire.

Il est **borné par construction** (`deque(maxlen=100)`) : pas de fuite mémoire possible.
Il enregistre aussi les **pannes**, sans quoi il ne montrerait que les succès et
mentirait sur ce qu'il annonce.

!!! warning "Volatile, et à protéger"
    Un redémarrage efface tout, et chaque worker ne voit que ses propres appels. C'est
    une fenêtre de débogage, pas un journal d'audit — Logfire garde la trace complète.
    Il **expose les questions et les réponses** : à protéger avant toute mise en
    production.

## Sous Windows

`curl` est un alias d'`Invoke-WebRequest` sous PowerShell. Utiliser `curl.exe` ou Git
Bash pour que les exemples ci-dessus fonctionnent tels quels.
