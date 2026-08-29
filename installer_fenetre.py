"""Installation de Jarvis, en fenetre.

Meme moteur que l'assistant console : les etapes viennent de `installer.py`,
qui reste la seule source. Cette fenetre ne fait que poser les questions
autrement et montrer l'avancement — dupliquer la logique reviendrait a devoir
corriger deux fois chaque defaut.

Trois pages : les reglages, l'installation, le resultat. Les services
facultatifs se depliaent seulement si on les coche, pour ne pas presenter
quinze champs a quelqu'un qui n'en veut aucun.

Rien n'est ecrit avant que l'installation ne demarre : tant qu'on n'a pas
clique, on peut revenir en arriere ou fermer sans trace.

Usage : python installer_fenetre.py
"""
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

RACINE = Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

import installer as moteur  # noqa: E402

FOND = "#0d1b24"
CARTE = "#132836"
TEXTE = "#d6ecf5"
FAIBLE = "#6f93a6"
ACCENT = "#2ee0ff"


class Sortie:
    """Detourne les impressions vers la fenetre, sans rien perdre."""

    def __init__(self, file):
        self.file = file

    def write(self, texte):
        if texte:
            self.file.put(texte)

    def flush(self):
        pass


class Assistant(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Installation de Jarvis")
        self.configure(bg=FOND)
        self.geometry("720x620")
        self.minsize(660, 560)

        self.champs = {}
        self.services = {}
        self.file = queue.Queue()

        self._styles()
        self._entete()
        self.corps = tk.Frame(self, bg=FOND)
        self.corps.pack(fill="both", expand=True, padx=26, pady=(4, 0))
        self._pied()
        self.page_reglages()

    # ------------------------------------------------------------ habillage

    def _styles(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("TCheckbutton", background=FOND, foreground=TEXTE,
                    focuscolor=FOND)
        s.configure("TCombobox", fieldbackground=CARTE, background=CARTE)
        s.configure("Barre.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=CARTE, borderwidth=0)

    def _entete(self):
        haut = tk.Frame(self, bg=FOND)
        haut.pack(fill="x", padx=26, pady=(22, 6))
        tk.Label(haut, text="JARVIS", bg=FOND, fg=ACCENT,
                 font=("Consolas", 21, "bold")).pack(anchor="w")
        tk.Label(haut, text="assistant vocal local  ·  avec l'interface MU-TH-UR",
                 bg=FOND, fg=FAIBLE, font=("Segoe UI", 10)).pack(anchor="w")

    def _pied(self):
        self.pied = tk.Frame(self, bg=FOND)
        self.pied.pack(fill="x", padx=26, pady=18)
        self.bouton = tk.Button(self.pied, text="Installer", command=self.demarrer,
                                bg=ACCENT, fg="#062028", relief="flat",
                                font=("Segoe UI", 11, "bold"), padx=26, pady=9,
                                cursor="hand2")
        self.bouton.pack(side="right")
        self.annuler = tk.Button(self.pied, text="Fermer", command=self.destroy,
                                 bg=CARTE, fg=FAIBLE, relief="flat",
                                 font=("Segoe UI", 10), padx=18, pady=9)
        self.annuler.pack(side="right", padx=(0, 10))

    def _vider(self):
        for w in self.corps.winfo_children():
            w.destroy()

    def _titre(self, parent, texte):
        tk.Label(parent, text=texte, bg=FOND, fg=TEXTE,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(14, 4))

    def _champ(self, parent, clef, libelle, defaut="", secret=False):
        ligne = tk.Frame(parent, bg=FOND)
        ligne.pack(fill="x", pady=3)
        tk.Label(ligne, text=libelle, bg=FOND, fg=FAIBLE, width=26,
                 anchor="w", font=("Segoe UI", 9)).pack(side="left")
        v = tk.StringVar(value=defaut)
        e = tk.Entry(ligne, textvariable=v, bg=CARTE, fg=TEXTE, relief="flat",
                     insertbackground=ACCENT, font=("Segoe UI", 10),
                     show="•" if secret else "")
        e.pack(side="left", fill="x", expand=True, ipady=4)
        self.champs[clef] = v
        return ligne

    def _service(self, parent, clef, libelle, explication, champs):
        """Un service facultatif : coche, et ses champs apparaissent."""
        bloc = tk.Frame(parent, bg=FOND)
        bloc.pack(fill="x", pady=(10, 0))
        actif = tk.BooleanVar(value=False)
        detail = tk.Frame(bloc, bg=FOND)

        def bascule():
            if actif.get():
                detail.pack(fill="x", padx=(24, 0), pady=(4, 0))
            else:
                detail.pack_forget()

        ttk.Checkbutton(bloc, text=libelle, variable=actif,
                        command=bascule).pack(anchor="w")
        tk.Label(bloc, text=explication, bg=FOND, fg=FAIBLE,
                 font=("Segoe UI", 8), wraplength=600,
                 justify="left").pack(anchor="w", padx=(24, 0))
        for c, lib, secret in champs:
            self._champ(detail, c, lib, secret=secret)
        self.services[clef] = actif

    # ------------------------------------------------------------ pages

    def page_reglages(self):
        self._vider()
        zone = tk.Canvas(self.corps, bg=FOND, highlightthickness=0)
        barre = ttk.Scrollbar(self.corps, orient="vertical", command=zone.yview)
        interieur = tk.Frame(zone, bg=FOND)
        interieur.bind("<Configure>",
                       lambda e: zone.configure(scrollregion=zone.bbox("all")))
        zone.create_window((0, 0), window=interieur, anchor="nw", width=640)
        zone.configure(yscrollcommand=barre.set)
        zone.pack(side="left", fill="both", expand=True)
        barre.pack(side="right", fill="y")

        self._titre(interieur, "L'essentiel")
        self._champ(interieur, "nom", "Ton prenom",
                    os.environ.get("USERNAME", "").title())
        ligne = tk.Frame(interieur, bg=FOND)
        ligne.pack(fill="x", pady=3)
        tk.Label(ligne, text="Qualite de transcription", bg=FOND, fg=FAIBLE,
                 width=26, anchor="w", font=("Segoe UI", 9)).pack(side="left")
        self.champs["whisper"] = tk.StringVar(value="small")
        ttk.Combobox(ligne, textvariable=self.champs["whisper"],
                     values=["tiny", "base", "small", "medium"],
                     state="readonly", width=12).pack(side="left")
        tk.Label(interieur,
                 text="small convient a la plupart des machines ; medium demande "
                      "une carte graphique.",
                 bg=FOND, fg=FAIBLE, font=("Segoe UI", 8)).pack(anchor="w",
                                                                padx=(210, 0))

        self._titre(interieur, "Services facultatifs")
        tk.Label(interieur,
                 text="Coche seulement ce que tu veux. Ce qui reste decoche sera "
                      "simplement absent, rien ne sera casse.",
                 bg=FOND, fg=FAIBLE, font=("Segoe UI", 9),
                 wraplength=620, justify="left").pack(anchor="w")

        self._service(interieur, "anthropic", "Cle Anthropic",
                      "Raisonnement nettement meilleur, mais payant. "
                      "Sans elle, le modele local suffit.",
                      [("anthropic_cle", "Cle API", True)])
        self._service(interieur, "spotify", "Spotify",
                      "Chercher et lancer de la musique a la voix. "
                      "Necessite une application creee sur developer.spotify.com.",
                      [("spotify_id", "Identifiant client", True),
                       ("spotify_secret", "Cle secrete", True)])
        self._service(interieur, "plex", "Plex",
                      "Lancer tes films et series, sur le PC ou sur une television.",
                      [("plex_hote", "Adresse du serveur", False),
                       ("plex_jeton", "Jeton Plex", True)])
        self._service(interieur, "mail", "Gmail",
                      "Lire tes messages a voix haute. Il faut un mot de passe "
                      "d'application, pas ton mot de passe habituel.",
                      [("mail_adresse", "Adresse Gmail", False),
                       ("mail_mdp", "Mot de passe d'application", True)])
        self.champs.setdefault("plex_hote", tk.StringVar(value="http://127.0.0.1:32400"))

        self._titre(interieur, "Interface")
        self.tel = tk.BooleanVar(value=True)
        ttk.Checkbutton(interieur,
                        text="Accessible depuis un telephone sur le meme reseau",
                        variable=self.tel).pack(anchor="w")
        self.chromecast = tk.BooleanVar(value=True)
        ttk.Checkbutton(interieur,
                        text="Utiliser les televisions Chromecast du reseau",
                        variable=self.chromecast).pack(anchor="w")

    def page_installation(self):
        self._vider()
        tk.Label(self.corps, text="Installation en cours", bg=FOND, fg=TEXTE,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10, 2))
        tk.Label(self.corps,
                 text="Une quinzaine de minutes, surtout des telechargements. "
                      "Tu peux laisser tourner.",
                 bg=FOND, fg=FAIBLE, font=("Segoe UI", 9)).pack(anchor="w")

        self.progres = ttk.Progressbar(self.corps, mode="indeterminate",
                                       style="Barre.Horizontal.TProgressbar")
        self.progres.pack(fill="x", pady=12)
        self.progres.start(14)

        self.journal = tk.Text(self.corps, bg="#08141c", fg=FAIBLE,
                               relief="flat", font=("Consolas", 9),
                               wrap="word", height=18)
        self.journal.pack(fill="both", expand=True)
        self.journal.configure(state="disabled")

    def page_fin(self, reussi, message=""):
        self._vider()
        couleur = ACCENT if reussi else "#e0574a"
        tk.Label(self.corps,
                 text="Installation terminee" if reussi else "Installation interrompue",
                 bg=FOND, fg=couleur,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(24, 8))
        if reussi:
            texte = (
                "Lance Jarvis par le raccourci du Bureau, puis dis « Hey Jarvis ».\n\n"
                "Interface        :  http://127.0.0.1:8770/\n"
                "Interface MU-TH-UR :  http://127.0.0.1:8770/mother\n\n"
                "Pour arreter proprement, utilise le raccourci « Arreter Jarvis ».\n"
                "Ne lance jamais deux Jarvis a la fois : il refusera de demarrer."
            )
        else:
            texte = (message or "Le journal ci-dessus indique ce qui a echoue.") + \
                "\n\nRien n'est casse : tu peux relancer cet assistant."
        tk.Label(self.corps, text=texte, bg=FOND, fg=TEXTE, justify="left",
                 font=("Segoe UI", 10)).pack(anchor="w")
        self.bouton.configure(text="Fermer", command=self.destroy,
                              state="normal")
        self.annuler.pack_forget()

    # ------------------------------------------------------------ travail

    def reglages(self):
        """Traduit les champs en dictionnaire de configuration."""
        v = {k: (x.get().strip() if isinstance(x, tk.StringVar) else x.get())
             for k, x in self.champs.items()}
        c = {
            "utilisateur": {"nom": v.get("nom") or "toi"},
            "assistant": {"personnalite": "jarvis"},
            "ollama": {"modele": moteur.MODELE_OLLAMA},
            "whisper": {"modele": v.get("whisper") or "small"},
            "voix_locale": "edge",
            "hud": {"port": 8770,
                    "hote": "0.0.0.0" if self.tel.get() else "127.0.0.1",
                    "https": True},
            "systeme": {"delai_extinction": 45},
        }
        if self.services["anthropic"].get() and v.get("anthropic_cle"):
            c["anthropic"] = {"cle": v["anthropic_cle"],
                              "modele": "claude-haiku-4-5"}
        if self.services["spotify"].get() and v.get("spotify_id"):
            c["spotify"] = {"client_id": v["spotify_id"],
                            "client_secret": v.get("spotify_secret", "")}
        if self.services["plex"].get() and v.get("plex_jeton"):
            c["plex"] = {"hote": v.get("plex_hote") or "http://127.0.0.1:32400",
                         "jeton": v["plex_jeton"]}
        if self.services["mail"].get() and v.get("mail_adresse"):
            c["mail"] = {"adresse": v["mail_adresse"],
                         "mot_de_passe_app": v.get("mail_mdp", "")}
        return c

    def demarrer(self):
        c = self.reglages()
        self.bouton.configure(state="disabled", text="Installation…")
        self.annuler.configure(state="disabled")
        self.page_installation()
        threading.Thread(target=self._travailler, args=(c,), daemon=True).start()
        self.after(120, self._vider_file)

    def _travailler(self, c):
        ancienne = sys.stdout
        sys.stdout = Sortie(self.file)
        try:
            moteur.verifier_python()
            py = moteur.environnement()
            moteur.ollama()
            moteur.voix()
            moteur.ecrire_config(c)
            moteur.recenser_applications(py)
            moteur.raccourcis(py)
            self.file.put("\x00fini")
        except SystemExit:
            self.file.put("\x00echec")
        except Exception as e:
            print(f"  Erreur inattendue : {e}")
            self.file.put("\x00echec")
        finally:
            sys.stdout = ancienne

    def _vider_file(self):
        fini = None
        while True:
            try:
                bout = self.file.get_nowait()
            except queue.Empty:
                break
            if bout.startswith("\x00"):
                fini = bout[1:]
                continue
            self.journal.configure(state="normal")
            self.journal.insert("end", bout)
            self.journal.see("end")
            self.journal.configure(state="disabled")

        if fini:
            self.progres.stop()
            self.page_fin(fini == "fini")
            return
        self.after(120, self._vider_file)


def main():
    if os.name != "nt":
        print("Cette fenetre vise Windows ; utilise installer.py ailleurs.")
    Assistant().mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            messagebox.showerror("Installation de Jarvis", str(e))
        except Exception:
            print("Erreur :", e)
