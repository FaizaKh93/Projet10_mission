# tests/test_conftest_garde.py
"""Vérifie que le garde-fou anti-appel-payant fonctionne réellement.

Un garde-fou qu'on n'a jamais vu se déclencher n'est pas un garde-fou.
"""
import pytest

from rag import pipeline


def test_un_appel_reel_est_refuse():
    """Le routeur utilise un vrai MistralModel : l'appeler doit lever, pas payer."""
    with pytest.raises(AssertionError, match="Appel réel à Mistral"):
        pipeline.routeur.run_sync("Une question quelconque ?")


def test_le_repli_du_routeur_ne_masque_pas_un_echec():
    """`router()` rattrape toute exception pour ne pas perdre la question. On vérifie
    que le repli est bien signalé dans le motif, et non silencieux."""
    route = pipeline.router("Une question quelconque ?")

    assert route.source == "les_deux"
    assert "indisponible" in route.motif
