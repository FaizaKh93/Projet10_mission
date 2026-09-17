# tests/conftest.py
import os
import sys
from pathlib import Path

import truststore

truststore.inject_into_ssl()  # même fix TLS proxy que le reste du projet

# Rend le package src/ importable (config.py, rag/vector_store.py), comme dans
# scripts/index.py et eval/evaluate_ragas.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


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
