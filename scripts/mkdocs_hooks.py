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
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
ORIGINAL = RACINE / "eval" / "analyze_results.ipynb"
COPIE = RACINE / "docs" / "notebook.ipynb"

log = logging.getLogger("mkdocs.hooks")


def on_pre_build(config, **kwargs) -> None:
    """Prépare ce que `docs/` ne contient pas en propre, avant que MkDocs ne le parcoure."""
    _copier_notebook()
    _generer_figures()


def _copier_notebook() -> None:
    if not ORIGINAL.exists():
        log.warning(
            f"{ORIGINAL.relative_to(RACINE)} introuvable : la page du notebook sera "
            "absente du site."
        )
        return

    COPIE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ORIGINAL, COPIE)  # copy2 préserve la date, donc la reconstruction incrémentale
    log.info(f"Notebook copié depuis {ORIGINAL.relative_to(RACINE)}")


def _generer_figures() -> None:
    """Les figures du comparatif sont produites depuis `eval/results/`.

    Import local et échec toléré : la construction du site ne doit pas dépendre de la
    présence de matplotlib, qui n'est utile qu'à cette étape.
    """
    # MkDocs charge ce fichier par son chemin : son dossier n'est pas sur sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import figures_rapport
    except ImportError as e:
        log.warning(f"Figures du comparatif non générées ({e}). Installer le groupe `docs`.")
        return

    figures_rapport.main()
    log.info("Figures du comparatif générées depuis eval/results/")
