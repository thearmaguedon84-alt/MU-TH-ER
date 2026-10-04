"""Banc d essai des raccourcis vocaux (core/raccourcis.py).

Chaque phrase doit rester reconnue par le bon raccourci. Deux sources :

- PHRASES ci-dessous : cas ecrits a la main, sans donnee personnelle
  (le depot est public) ;
- logs/jarvis.log : toutes les phrases reellement reconnues par le passe.
  Le journal n est pas versionne, ces cas ne tournent donc que sur le PC
  de Jarvis — c est voulu, ils contiennent des noms et des propos prives.

Aucune action n est executee : les fonctions des outils (lancer un film,
generer une image, caster...) sont remplacees par des doublures muettes.
Seules les aides qui servent a DECIDER (plateformes connues, personnages,
applications de config.yaml) restent les vraies.

Lancer :  .venv\\Scripts\\python.exe -m unittest tests.test_raccourcis -v
"""

import importlib
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from core import raccourcis  # noqa: E402

JOURNAL = RACINE / "logs" / "jarvis.log"

# Phrase -> raccourci attendu. Ecrites comme on les dit, fautes de dictee
# comprises : c est precisement ce qu il faut tenir.
PHRASES = {
    "envoie moi la derniere video par mail": "_video_mail",
    "envoi moi le dernier dessin anime par mail": "_video_mail",
    "quelle heure est il": "_heure",
    "fais un dessin anime south park ou gerald dit a cartman salut les gars":
        "_dessin_anime_dicte",
    "realise un dessin annime soouth park ou gerald dit a cartman tu te prend pour qui":
        "_dessin_anime_dicte",
    "fais moi un dessin anime avec le script bus txt": "_dessin_anime",
    # Relevees dans le journal du 24/09 : parties vers le modele.
    "envoie la derniere video sur tv en bas": "_video_existante",
    "envoi moi la derniere video realisee": "_video_existante",
    "envoi moi la dernier videobpar mail": "_video_mail",
    "lance peppa pig sur tv en bas de netfix": "_diffuser_service",
    "egenere moi une image d un crane": "_image",
    "envoie moi la derniere image par mail": "_image_mail",
    # 03/10 : South Park du jour, personnages et voix sans passer par Claude.
    "fais le south park du jour": "_south_park_du_jour",
    "publie le south park": "_south_park_du_jour",
    "poste la video du jour sur tiktok": "_south_park_du_jour",
    "jette le south park du jour": "_south_park_du_jour",
    "fais un dessin anime avec serviettesky et gerald sur l actualite":
        "_south_park_du_jour",
    "ajoute le personnage mr mackey c est un adulte": "_reglage_personnage",
    "ajoute le personnage token": "_reglage_personnage",
    "monte la bouche de kyle de deux crans": "_reglage_personnage",
    "la bouche de chef plus a gauche": "_reglage_personnage",
    "descends la bouche de madame garrison": "_reglage_personnage",
    "butters est un adulte": "_reglage_personnage",
    "fais cartman plus grand": "_reglage_personnage",
    "montre moi kyle": "_reglage_personnage",
    "entraine la voix de serviettesky hors ligne": "_entrainement_nuit",
    "bloque les bras de garrison": "_reglage_personnage",
    "les bras de kyle mobiles": "_reglage_personnage",
    # Une replique dictee n est jamais un ordre.
    "fais un dessin anime south park ou cartman dit publie le south park":
        "_dessin_anime_dicte",
}

# Phrases du journal dont le raccourci a change volontairement depuis.
# Debut de phrase (tel qu ecrit dans le journal) -> nouveau raccourci.
# Seulement des debuts neutres : le reste des phrases est prive.
CHANGEMENTS_VOULUS = {
    # Autrefois pris pour un envoi d image, c est une generation.
    "egenere moi une image": "_image",
    # Dessin anime dicte avec fautes : c etait _image, a tort.
    "fait moi un dessin annime south parck avec": "_dessin_anime_dicte",
    # Un dessin anime nomme est une video, pas une image.
    "envoi moi le dessin annime toto3 par mail": "_video_existante",
    # Une chose deja faite : on l envoie, on ne la refait pas. Avant, ces
    # phrases relancaient un rendu complet du dernier script.
    "cast le dernier dessin anime sur": "_video_existante",
    "envoie moi le dernier dessin anime": "_video_existante",
    # Un dialogue dicte (« X dit ... ») : le script s ecrit depuis la phrase,
    # au lieu de rejouer le dernier script range.
    "genere moi un dessin anime south park ou": "_dessin_anime_dicte",
    "fais moi un dessin anime south park ou": "_dessin_anime_dicte",
    "fais moi un dessin anime southpark": "_dessin_anime_dicte",
    "fait moi un dessin anime southpark": "_dessin_anime_dicte",
    "envoi moi le dessin animes cree une fois": "_dessin_anime_dicte",
    # 26/09 : « qui di » (t avale) est une replique ; et une scene dictee
    # incomprise repond au lieu de partir en image ou en video.
    "fais un dessins annime avec cartman qui di": "_dessin_anime_dicte",
    "fais moi un dessin annime southpark avec tous les personnages":
        "_dessin_anime_dicte",
    "realise un dessin anime southpark avec tous les personnages":
        "_dessin_anime_dicte",
}

# Aides qui decident sans agir : on garde les vraies.
AIDES = {"reconnaitre", "PLATEFORMES", "PERSONNAGES", "_plat", "_note",
         "_DERNIERE", "_DERNIER_DESSIN", "DOSSIER", "_apps", "_trouver",
         "visages_connus", "chercher", "derniere_image", "_resoudre"}


def _outils_importes():
    """(module, nom) de chaque « from tools.x import y » de raccourcis.py."""
    source = Path(raccourcis.__file__).read_text(encoding="utf-8")
    trouves = set()
    for module, noms in re.findall(
            r"from (tools\.\w+) import \(?([^)\n]*(?:\n[^)\n]*)*?)\)?\s*$",
            source, re.M):
        for nom in re.split(r"[\s,()]+", noms.split(" as ")[0].strip()):
            if nom.isidentifier():
                trouves.add((module, nom))
    for module, noms in re.findall(
            r"from (tools\.\w+) import \(([^)]*)\)", source):
        for nom in re.split(r"[\s,]+", noms.strip()):
            if nom.isidentifier():
                trouves.add((module, nom))
    return trouves


def _ecran(nom):
    c = mock.MagicMock()
    c.cast_info.friendly_name = nom
    c.app_id = None
    return c


ECRANS = [_ecran(n) for n in ("TV en bas", "Salon", "Chambre",
                              "Videoprojecteur")]
_RUSTINES = []
_TRACES = []


def setUpModule():
    for module, nom in sorted(_outils_importes()):
        if nom in AIDES:
            continue
        mod = importlib.import_module(module)
        if not callable(getattr(mod, nom, None)):
            continue
        _RUSTINES.append(mock.patch.object(
            mod, nom, mock.MagicMock(return_value="ok (%s)" % nom)))
    cast = importlib.import_module("tools.cast")
    _RUSTINES.append(mock.patch.object(cast, "_decouvrir",
                                       lambda *a, **k: ECRANS))
    _RUSTINES.append(mock.patch.object(
        cast, "_choisir", lambda nom=None: cast._parmi(ECRANS, nom)
        if nom else ECRANS[0]))
    # Pas de reseau pour les chaines : le cache disque ou rien.
    canal = importlib.import_module("tools.canal")
    _RUSTINES.append(mock.patch.object(
        canal, "index", lambda forcer=False: _chaines_en_cache(canal)))
    _RUSTINES.append(mock.patch.object(
        raccourcis, "_tracer", lambda quoi, detail="": _TRACES.append(quoi)))
    # raccourcis.py appelle aussi « from tools import spotify as S » puis
    # S.spotify_jouer(...) : ces appels echappent a l analyse ci-dessus.
    for module, noms in (
            ("tools.spotify", ("spotify_appareils", "spotify_controle",
                               "spotify_en_cours", "spotify_jouer",
                               "spotify_transferer")),
            ("tools.plex", ("plex_chercher", "plex_jouer", "plex_musique"))):
        mod = importlib.import_module(module)
        for nom in noms:
            _RUSTINES.append(mock.patch.object(
                mod, nom, mock.MagicMock(return_value="ok (%s)" % nom)))
    spotify = importlib.import_module("tools.spotify")
    _RUSTINES.append(mock.patch.object(spotify, "configure", lambda: True))
    # Le HUD et config.yaml ne doivent pas bouger pendant l essai.
    hud = mock.MagicMock()
    _RUSTINES.append(mock.patch.dict(sys.modules, {"hud": hud}))
    config = importlib.import_module("core.config")
    _RUSTINES.append(mock.patch.object(config, "definir", mock.MagicMock()))
    # Filet : tout lancement de programme ou de page est une fuite. Il leve
    # une erreur, que essayer() trace comme une panne — le test echoue.
    import os
    import subprocess
    import webbrowser

    def fuite(*a, **k):
        raise RuntimeError("action reelle pendant le test : %r" % (a[:1],))
    _RUSTINES.append(mock.patch.object(subprocess, "Popen", fuite))
    _RUSTINES.append(mock.patch.object(subprocess, "run", fuite))
    _RUSTINES.append(mock.patch.object(webbrowser, "open", fuite))
    if hasattr(os, "startfile"):
        _RUSTINES.append(mock.patch.object(os, "startfile", fuite))
    for r in _RUSTINES:
        r.start()


def tearDownModule():
    for r in reversed(_RUSTINES):
        r.stop()


def _chaines_en_cache(canal):
    import json
    try:
        with open(canal._CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def quel_raccourci(phrase):
    """Nom du raccourci retenu ("aucun" si le modele prendrait la main),
    et la liste des pannes rencontrees en chemin."""
    _TRACES.clear()
    raccourcis.essayer(phrase)
    pannes = [t for t in _TRACES if t.startswith("panne")]
    fin = _TRACES[-1] if _TRACES else "aucun"
    return ("aucun" if fin.startswith("aucun") else fin), pannes


def phrases_du_journal():
    """{phrase: raccourci} de toutes les reconnaissances passees."""
    if not JOURNAL.exists():
        return {}
    cas = {}
    motif = re.compile(r"raccourci (_\w+) : (.+)$")
    for ligne in JOURNAL.read_text(encoding="utf-8",
                                   errors="replace").splitlines():
        m = motif.search(ligne)
        if m:
            # La derniere reconnaissance d une phrase fait foi.
            cas[m.group(2).strip()] = m.group(1)
    for phrase in cas:
        for debut, nouveau in CHANGEMENTS_VOULUS.items():
            if phrase.startswith(debut):
                cas[phrase] = nouveau
    return cas


class Raccourcis(unittest.TestCase):

    def verifier(self, cas):
        for phrase, attendu in cas.items():
            with self.subTest(phrase=phrase[:90]):
                obtenu, pannes = quel_raccourci(phrase)
                self.assertEqual(pannes, [], "un raccourci a plante")
                self.assertEqual(obtenu, attendu)

    def test_phrases_ecrites(self):
        self.verifier(PHRASES)

    def test_gestes_dictes(self):
        """Les deplacements dictes deviennent des lignes (QUI geste), a leur
        place dans le dialogue ; un verbe dans une replique reste du texte."""
        import tools.dessin_anime as D
        cas = {
            "fais un dessin anime south park ou cartman entre par la gauche "
            "et dit salut les gars puis gerald marche vers cartman et dit "
            "tu te prends pour qui puis cartman sort a droite": [
                "(CARTMAN entre par la gauche)", "CARTMAN: salut les gars",
                "(GERALD marche vers cartman)",
                "GERALD: tu te prends pour qui", "(CARTMAN sort a droite)"],
            "fais un dessin anime south park ou cartman dit kenny va mourir "
            "et kyle repond non": ["CARTMAN: kenny va mourir", "KYLE: non"],
            "dessin anime south park ou kenny traverse stan saute et dit "
            "ouah": ["(KENNY traverse)", "(STAN saute)", "STAN: ouah"],
            # 26/09 : gestes sans « et », fautes de dictee sur les noms.
            "dessin annime southpark serviettesky entre par la droite "
            "serviettesky dit je ne suis pas une serviette gerald entre par "
            "la gauche gerarld dit tu es une serviette": [
                "(SERVIETTE entre par la droite)",
                "SERVIETTE: je ne suis pas une serviette",
                "(GERALD entre par la gauche)",
                "GERALD: tu es une serviette"],
            # 26/09 : dicte comme un script, le nom puis la replique, sans
            # « dit » ; « une serviette » reste dans la replique.
            "dessin annime southpark serviette entre par la droite gerald "
            "entre par la gauche serviette je ne suis pas une serviette "
            "gerald tu es une serviette": [
                "(SERVIETTE entre par la droite)",
                "(GERALD entre par la gauche)",
                "SERVIETTE: je ne suis pas une serviette",
                "GERALD: tu es une serviette"],
            # 28/09 : « garisson » a un seul r etait pris pour Mr Garrison.
            "dessin anime south park madame garisson entre par la droite "
            "madame garisson dit bonjour et garrison dit salut": [
                "(MRSGARRISON entre par la droite)",
                "MRSGARRISON: bonjour", "GARRISON: salut"],
            "dessin anime south park cartman salut les gars kyle va te faire "
            "voir cartman marche vers kyle": [
                "CARTMAN: salut les gars", "KYLE: va te faire voir",
                "(CARTMAN marche vers kyle)"],
        }
        for phrase, attendu in cas.items():
            with self.subTest(phrase=phrase[:60]):
                D.dessin_anime.reset_mock()
                self.assertEqual(quel_raccourci(phrase)[0],
                                 "_dessin_anime_dicte")
                script = D.dessin_anime.call_args.kwargs["script"]
                self.assertEqual(script.strip().split("\n")[1:], attendu)
                # Et le moteur de dessin comprend chaque geste.
                for ligne in attendu:
                    if ligne.startswith("("):
                        self.assertIsNotNone(D._lire_action(ligne[1:-1]),
                                             ligne)

    def test_confirmation_avant_publication(self):
        """« publie » et l entrainement hors ligne demandent l accord :
        rien ne part tant que l utilisateur n a pas dit oui."""
        from core import registre
        import tools.south_park_du_jour as S
        import tools.entrainer_voix as E
        for phrase, outil, module, fonction in (
                ("publie le south park", "publier_south_park", S,
                 "publier_south_park"),
                ("entraine la voix de kyle hors ligne",
                 "entrainer_voix_hors_ligne", E, "entrainer_voix_hors_ligne")):
            with self.subTest(phrase=phrase):
                registre.annuler_confirme()
                with mock.patch.object(registre.get(outil), "fonction",
                                       mock.MagicMock()) as appel:
                    reponse = raccourcis.essayer(phrase)
                    self.assertIn("Tu confirmes", reponse)
                    appel.assert_not_called()
                    self.assertIsNotNone(registre.annonce_en_attente())
                registre.annuler_confirme()

    @unittest.skipUnless(JOURNAL.exists(), "pas de journal sur ce poste")
    def test_phrases_du_journal(self):
        self.verifier(phrases_du_journal())


if __name__ == "__main__":
    unittest.main(verbosity=2)
