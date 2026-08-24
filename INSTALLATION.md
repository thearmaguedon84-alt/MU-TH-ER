# Jarvis — assistant vocal local

Un assistant qui écoute, répond et agit sur ton PC. Le modèle tourne **chez
toi** : aucune de tes phrases ne part sur internet, sauf si tu actives
explicitement la recherche web ou une clé d'API.

Il vient avec une seconde interface, **MU-TH-UR**, qui reprend l'esthétique du
terminal de bord d'*Alien*.

## Ce qu'il faut avant de commencer

- **Windows 10 ou 11**
- **Python 3.11 ou plus récent** — python.org, en cochant *Add Python to PATH*
- **8 Go de RAM** au minimum, 16 recommandés
- **10 Go d'espace libre** : le modèle en occupe 5, les dépendances autant
- Une **carte graphique NVIDIA** accélère beaucoup la transcription, sans être
  obligatoire

## Installation

1. Décompresse l'archive où tu veux — un dossier sans espaces ni accents dans
   le chemin évite bien des ennuis.
2. Choisis ton assistant :

   - **`INSTALLER_FENETRE.bat`** — une fenetre avec des cases a cocher et des
     champs. Le plus simple.
   - **`INSTALLER.bat`** — le meme assistant en fenetre console, questions
     posees une par une.

   Les deux font exactement la meme chose. Si tu preferes la ligne de commande :

```
python installer.py           # console
python installer_fenetre.py   # fenetre
```

3. Réponds aux questions. **Tout est facultatif sauf les deux premières
   étapes** : refuser Spotify n'empêche rien d'autre de fonctionner.

Compte une quinzaine de minutes, essentiellement en téléchargements. Tu peux
relancer l'installation sans rien casser.

## Ce qu'on te demandera

| Question | Nécessaire ? | Pour quoi faire |
|---|---|---|
| Ton prénom | oui | Il t'appelle par ton nom |
| Modèle de transcription | oui | `small` convient à la plupart des machines |
| Clé Anthropic | non | Raisonnement nettement meilleur, mais payant |
| Spotify | non | Chercher et lancer de la musique à la voix |
| Plex | non | Lancer tes films et séries |
| Gmail | non | Lire tes messages à voix haute |
| Chromecast | non | Envoyer l'interface et la vidéo sur tes télés |

Pour Gmail, il faut un **mot de passe d'application**, pas ton mot de passe
habituel : `myaccount.google.com` → *Sécurité* → *Mots de passe des
applications*.

## Premiers pas

Lance **Jarvis** depuis le raccourci du Bureau, puis dis **« Hey Jarvis »** et
parle.

Quelques phrases pour commencer :

- « quelle heure est-il »
- « quel temps fait-il demain »
- « ouvre Chrome »
- « mets un minuteur de dix minutes »
- « cherche sur internet qui a gagné hier soir »
- « éteins le PC »
- « mode maman » pour passer sur MU-TH-UR, « mode jarvis » pour revenir

Les interfaces s'ouvrent dans un navigateur :

- Jarvis : `http://127.0.0.1:8770/`
- MU-TH-UR : `http://127.0.0.1:8770/mother`

## Depuis un téléphone

Sur le même réseau Wi-Fi, ouvre `https://<adresse-du-PC>:8771/tel`. Le
certificat étant auto-signé, ton navigateur affichera un avertissement à
accepter une fois.

Pour y accéder **de l'extérieur**, le plus simple est un réseau privé
[Tailscale](https://tailscale.com) : installe-le sur le PC et sur le téléphone
avec le même compte, et le PC devient joignable de partout sans ouvrir le
moindre port sur ta box.

Une fois la page ouverte, menu **⋮** → *Installer l'application* : tu obtiens
une icône, comme une vraie application.

## Deux règles à retenir

**Ne lance jamais deux Jarvis à la fois.** Il refusera de démarrer et te le
dira. Deux instances se disputent les ports et provoquent des pannes qui n'en
sont pas — des pages introuvables, des réponses périmées.

**Ne partage jamais ton `config.yaml`.** Il contient tes identifiants. Si tu
veux transmettre Jarvis à quelqu'un, utilise `preparer_paquet.py`, qui
reconstruit une archive propre et refuse de la produire s'il y trouve la
moindre donnée personnelle.

## Si ça ne marche pas

**Il n'entend rien** — vérifie le micro par défaut de Windows. Le mot
d'activation demande une diction nette : « hey JAR-vis ».

**Il répond très lentement** — le modèle de 7 milliards de paramètres est
exigeant. Passe le modèle de transcription à `base`, ou installe un modèle plus
léger avec `ollama pull qwen2.5:3b` puis modifie `ollama.modele` dans
`config.yaml`.

**Une page affiche 404** — tu as probablement deux Jarvis lancés. Utilise le
raccourci *Arrêter Jarvis*, puis relance une seule fois.

**Le téléphone ne se connecte pas** — le pare-feu Windows bloque le port. Dans
une invite administrateur :

```
New-NetFirewallRule -DisplayName "Jarvis" -Direction Inbound -LocalPort 8770-8771 -Protocol TCP -Action Allow -Profile Private
```
