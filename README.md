# Jarvis

Un assistant vocal qui tourne **entièrement sur votre machine**. Il écoute, il
répond, il agit. Aucune de vos phrases ne part sur internet — sauf si vous
activez explicitement la recherche web.

Il vient avec une seconde interface, **MU-TH-UR**, hommage au terminal de bord
du *Nostromo*.

![L'interface MU-TH-UR](docs/images/muthur.png)

## Ce qu'il sait faire

**Parler et comprendre** — mot d'activation, transcription locale par Whisper,
réponse par un modèle qui tourne chez vous via Ollama. Une clé Anthropic peut
être ajoutée pour un raisonnement plus fin, mais rien ne l'exige.

**Lancer ce qui est installé** — applications, jeux Steam, programmes du
Microsoft Store, recensés automatiquement sur la machine.

**Piloter les médias** — Spotify, Plex, YouTube, vos fichiers vidéo locaux.

**Envoyer sur les télévisions** — Chromecast, y compris myCANAL, Netflix et
Prime Video. Chacun de ces services a demandé une approche différente : là où
myCANAL et Netflix acceptent qu'une page ouvre elle-même sa session de
diffusion, Prime n'expose rien et passe par une recopie d'écran.

**Chercher sur le web** — l'outil rapporte des faits datés et écarte le hors
sujet, plutôt que de rendre une liste de sites.

**Commander l'ordinateur** — extinction, redémarrage, verrouillage, volume,
avec un délai pendant lequel on peut se raviser.

**Créer des images, de la musique, des vidéos** — tout en local, sur la carte
graphique de la machine. Une image ordinaire en quelques secondes, une image
soignée par Flux en une minute, un morceau chanté de quarante-cinq secondes en
trois minutes, cinq secondes de vidéo en sept. Les créations se rangent dans
`Images`, `Musique`, `Vidéos` et `Documents`, dans un sous-dossier `MU-TH-UR`.

**Mettre quelqu'un dans une scène** — à partir d'une photo de référence, le
visage est porté sur une scène inventée, ou transposé sur une image existante.
La ressemblance est *mesurée* et annoncée, pas laissée à l'appréciation : deux
empreintes de visage, leur cosinus, et au-delà de 0,5 c'est la même personne.

**Monter un clip** — des images ou des séquences vidéo assemblées sur un
morceau, calées sur sa durée, enchaînées en fondu. Les séquences sont jouées
puis rejouées à l'envers, ce qui comble sans laisser voir la reprise.

**Depuis un téléphone** — deux interfaces web installables comme des
applications, avec dictée vocale. Joignables de partout via un réseau privé.

## Installation

Windows 10 ou 11, Python 3.11 ou plus récent, 8 Go de RAM, 10 Go d'espace
libre. Une carte NVIDIA accélère nettement la transcription sans être
obligatoire.

Téléchargez l'archive, décompressez, et double-cliquez :

- **`INSTALLER_FENETRE.bat`** — assistant en fenêtre
- **`INSTALLER.bat`** — le même en console

![L'assistant d'installation](docs/images/installateur.png)

L'assistant vérifie les prérequis, installe les dépendances et le modèle,
télécharge la voix française, pose les questions nécessaires, recense vos
applications et place les raccourcis. Une quinzaine de minutes, surtout du
téléchargement.

**Tout est facultatif sauf le cœur.** Refuser Spotify n'empêche rien d'autre de
fonctionner : la commande sera simplement absente.

Le détail se trouve dans [INSTALLATION.md](INSTALLATION.md).

## Quelques phrases pour commencer

```
Hey Jarvis, quelle heure est-il
Hey Jarvis, ouvre Chrome
Hey Jarvis, mets Arte sur la télé du bas
Hey Jarvis, mets Stranger Things sur Netflix sur la télé du bas
Hey Jarvis, cherche sur internet qui a gagné hier soir
Hey Jarvis, fais-moi une image très soignée d'un phare dans la tempête
Hey Jarvis, compose une chanson douce à la guitare
Hey Jarvis, monte un clip avec mes vidéos de xénomorphe
Hey Jarvis, mode maman
Hey Jarvis, éteins le PC
```

## Comment c'est fait

Le cœur est une chaîne simple : détection du mot d'activation, transcription,
puis un modèle de langage avec des outils.

Ce qui compte davantage, c'est ce qui se trouve **avant** le modèle. Un
assistant vocal domestique tourne sur un petit modèle, qui se trompe d'outil et
invente des arguments. Les commandes courantes passent donc par une chaîne de
raccourcis déterministes — des expressions régulières et de la recherche floue —
et le modèle n'est sollicité que pour ce qui n'a pas été reconnu. Le résultat
est plus rapide et beaucoup plus fiable.

Le même principe gouverne les outils : ils font le travail complet plutôt que
d'attendre du modèle qu'il enchaîne les étapes. La recherche web, par exemple,
juge elle-même si les extraits répondent, et va lire la page dans le cas
contraire.

Les intégrations reposent sur ce que les services exposent réellement, relevé
en observant leur trafic plutôt qu'en devinant. Les notes de ces relevés sont
dans `recettes/` — le catalogue de myCANAL par EpgId, la recherche GraphQL de
Netflix, la page de lecture de Prime.

## Fabriquer des images, du son, des vidéos

Cette partie ne s'installe pas avec le reste et ne s'y substitue pas : ce sont
quatre moteurs séparés, que Jarvis démarre et arrête lui-même selon les
besoins. Comptez une quarantaine de giga-octets de modèles et une carte
NVIDIA de 12 Go. Sans eux, tout le reste fonctionne — les commandes
correspondantes sont simplement absentes.

| Ce qu'on demande | Moteur | Sur une RTX 3060 |
|---|---|---|
| Une image | Stable Diffusion (SDXL) | 15 à 30 s |
| Une image très soignée | Flux, quantifié | 45 s ; 7 min en qualité maximale |
| Un morceau chanté | ACE-Step 1.5 | 45 s de musique en 3 min |
| Une vidéo | Wan 2.2 image-vers-vidéo | 5 s de vidéo en 7 min |
| Un montage de clip | ffmpeg | quelques secondes |

**Une seule chose à la fois.** Douze giga-octets ne suffisent pas à deux
modèles, et deux modèles qui se disputent la carte ne sont pas deux fois plus
lents mais dix fois. Un arbitre décharge donc ce qui ne sert plus avant de
charger ce qui va servir, et une file d'attente empêche deux demandes de se
marcher dessus. Une demande qui doit patienter le dit.

**Les mains et les visages sont repris automatiquement.** C'est le défaut le
plus visible des images générées ; une passe de correction ciblée le règle
pour une quinzaine de secondes.

**Flux plutôt que SDXL quand ça compte.** Il tient le texte écrit dans l'image
et les scènes où beaucoup d'éléments doivent s'accorder, là où SDXL invente. Il
coûte plus cher, donc on le demande explicitement : « une image très soignée ».

**Un visage se travaille en gros plan.** Transposer un visage sur l'image
entière ne marche que si ce visage occupe déjà le cadre : redimensionnée, une
tête qui tient dans deux pour cent de l'image ne conserve pas assez de pixels
pour être reconnaissable. On découpe donc autour du visage, on travaille en
gros plan, et on recolle — en n'empruntant que la peau, d'après les points de
contour, pour que les cheveux de la photo restent devant. Le raccord se fond
par équation de Poisson, et la netteté comme le grain sont alignés sur ceux de
la photo d'accueil. Sans ces trois accords, le résultat ressemble à un
autocollant.

## Ce que ça ne fait pas

Ce n'est pas un produit. C'est un projet personnel, écrit pour une maison, et
il s'en ressent : Windows uniquement, français uniquement, et certaines
intégrations supposent une installation qui ressemble à la mienne.

Les services de streaming changent leurs interfaces sans prévenir. Ce qui
marche aujourd'hui peut cesser demain — les relevés dans `recettes/` sont là
pour rendre la réparation possible.

## Licence

Code sous licence MIT — voir [LICENSE](LICENSE).

L'esthétique MU-TH-UR est un **hommage non commercial** à *Alien* (1979). Le
nom, le logo Nostromo et l'univers appartiennent à 20th Century Studios ; ce
projet n'est ni affilié ni approuvé par eux, et n'est vendu sous aucune forme.
