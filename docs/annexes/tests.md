# Annexe — Tests et couverture

**177 tests gratuits, 83 % de couverture.** Deux suites, séparées par leur coût.

```bash
uv run pytest                          # les 177 gratuits
uv run pytest -m api                   # 9 tests, appels réels facturés
uv run pytest --cov --cov-report=html  # rapport dans htmlcov/
```

## Trois natures de tests

| Nature | Ce qu'ils couvrent |
|---|---|
| **unitaires** | contrats Pydantic, garde-fous SQL, construction du contexte transmis au juge |
| **fonctionnels** | l'API de bout en bout, l'interface web, la chaîne complète, les scripts |
| **intégration réelle** | 4 fichiers marqués `api` : la vraie API Mistral |

## Rien de payant ne part par accident

C'est appliqué par le code, pas par la vigilance :

- les tests `api` sont **exclus par défaut** (`addopts = "-m 'not api'"`) ;
- ils sont **sautés** si `MISTRAL_API_KEY` est absente ;
- un garde-fou du `conftest` fait **échouer** tout appel réel à Mistral depuis la suite
  gratuite.

!!! note "Une exclusion par défaut ne suffit pas"
    Un test peut atteindre une API réelle sans porter le marqueur, par une dépendance
    indirecte. C'est pourquoi le garde-fou agit au niveau du client HTTP et non du
    marqueur : il rend l'appel impossible plutôt que déconseillé.

## La couverture

Elle porte sur `src/`, `app/`, `scripts/` **et** `eval/`, sans exception.

| Module | Couverture |
|---|---|
| `src/db_models.py` | 100 % |
| `scripts/index.py` | 100 % |
| `src/rag/sql_tool.py` | 99 % |
| `src/schemas.py` | 99 % |
| `app/api.py` | 96 % |
| `src/rag/pipeline.py` | 96 % |
| `src/rag/generation.py` | 93 % |
| `src/rag/vector_store.py` | 65 % |
| `src/loading/loaders.py` | 63 % |
| `eval/evaluate_ragas.py` | 61 % |
| **Total** | **83 %** |

Les deux derniers s'expliquent : `evaluate_ragas.py` a une boucle `main()` qui exige les
deux API payantes, et `loaders.py` contient les chemins d'extraction pour des formats
que ce projet n'utilise pas.

!!! abstract "Aucun module n'est exclu de la mesure"
    Un rapport de couverture dont on retire ce qui ferait baisser la moyenne ne mesure
    plus rien. Le harnais d'évaluation y figure donc au même titre que le reste, avec
    le taux qui est le sien.

## Des tests qui contraignent, pas qui décorent

Deux exemples de propriétés figées parce que leur rupture serait **silencieuse** :

**Le périmètre d'indexation.** `EXTENSIONS_INDEXEES` vit dans la configuration, et
l'indexation doit l'appliquer sans jamais la redéfinir. Un test le vérifie — et une
mutation le confirme : remplacer la référence par une liste écrite en dur fait échouer
la suite. Sans lui, réintroduire le classeur dans l'index ne provoquerait aucune alerte.

**Le contexte transmis au juge.** Aucun cas ne doit partir au juge sans contexte, quelle
que soit la route. Onze tests couvrent les quatre routes, y compris celui d'une requête
qui rend zéro ligne — l'absence de résultat étant une information, pas un vide.

Ces deux propriétés ont un point commun : leur rupture ne lève aucune erreur. Elle se
manifesterait comme une baisse inexpliquée de qualité, des semaines plus tard.
