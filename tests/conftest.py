# tests/conftest.py
import os
import sys
from pathlib import Path

import pytest
import truststore
from dotenv import load_dotenv

truststore.inject_into_ssl()  # même fix TLS proxy que le reste du projet

# Rend le package src/ importable (config.py, rag/vector_store.py), comme dans
# scripts/index.py et eval/evaluate_ragas.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Chargé ici et pas seulement via config.py : le hook ci-dessous lit MISTRAL_API_KEY
# dès la collecte, avant qu'un test ait importé config. Sans ça, le skip dépendrait
# d'un effet de bord — de quel module de test importe config le premier, donc de la
# sélection lancée.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# pytest_collection_modifyitems : hook spécial que pytest appelle automatiquement
# juste après avoir collecté tous les tests, avant de les exécuter - permet ici
# d'ajouter dynamiquement un marqueur "skip" à certains tests
def pytest_collection_modifyitems(config, items):
    """Si MISTRAL_API_KEY n'est pas définie, saute proprement les tests "api"
    au lieu de les faire échouer avec une erreur d'authentification confuse."""
    if os.getenv("MISTRAL_API_KEY"):
        return  # clé présente, on ne touche à rien
    import pytest

    skip_api = pytest.mark.skip(reason="MISTRAL_API_KEY non définie dans .env")
    # "items" = tous les tests collectés ; on ajoute le skip seulement à ceux
    # marqués @pytest.mark.api (via item.keywords, qui contient tous les marqueurs
    # du test), les autres tests éventuels resteraient inchangés
    for item in items:
        if "api" in item.keywords:
            item.add_marker(skip_api)


@pytest.fixture(autouse=True)
def aucun_appel_paye(request, monkeypatch):
    """Interdit tout appel réel à Mistral dans la suite gratuite.

    Posé après un incident : brancher le routeur a suffi pour que quatre tests
    d'interface appellent l'API sans que personne ne le remarque — seul le temps
    d'exécution avait triplé. Un test qui coûte de l'argent doit le déclarer par le
    marqueur `api`, jamais l'obtenir par inadvertance.

    Le remplacement se fait sur la classe : il couvre donc aussi les agents créés à
    l'import, avant que le test ne s'exécute.
    """
    if request.node.get_closest_marker("api"):
        return  # tests payants, explicitement marqués

    from pydantic_ai.models.mistral import MistralModel

    async def refuser(self, *args, **kwargs):
        raise AssertionError(
            "Appel réel à Mistral depuis la suite gratuite. Remplacer le modèle par un "
            "FunctionModel, ou marquer le test @pytest.mark.api s'il doit vraiment payer."
        )

    monkeypatch.setattr(MistralModel, "request", refuser)
    monkeypatch.setattr(MistralModel, "request_stream", refuser, raising=False)
