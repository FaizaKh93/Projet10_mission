# scripts/mkdocs_hooks.py
"""Crochets MkDocs.

Le notebook d'analyse vit dans `eval/`, à côté des résultats qu'il lit. MkDocs ne
publie que ce qui se trouve sous `docs/`, et le recopier à la main le ferait diverger
de l'original — c'est exactement le genre de duplication que ce projet évite ailleurs.

Il est donc copié à chaque construction. La copie est jetable et ignorée par git :
l'original reste la seule version qu'on modifie.
"""
import logging
import shutil
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
ORIGINAL = RACINE / "eval" / "analyze_results.ipynb"
COPIE = RACINE / "docs" / "notebook.ipynb"

log = logging.getLogger("mkdocs.hooks")


def on_pre_build(config, **kwargs) -> None:
    """Copie le notebook avant que MkDocs ne parcoure `docs/`."""
    if not ORIGINAL.exists():
        log.warning(
            f"{ORIGINAL.relative_to(RACINE)} introuvable : la page du notebook sera "
            "absente du site."
        )
        return

    COPIE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ORIGINAL, COPIE)  # copy2 préserve la date, donc la reconstruction incrémentale
    log.info(f"Notebook copié depuis {ORIGINAL.relative_to(RACINE)}")
