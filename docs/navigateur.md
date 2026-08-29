# Assistance sur ton navigateur Chrome

Jarvis peut t'assister pendant que tu surfes sur **ton** Chrome : ouvrir un
onglet, resumer/traduire la page active, gerer les onglets, et agir (clic,
scroll, remplir un champ) pilote par Claude.

C'est distinct de la **reservation** (docs/reservation.md), qui tourne sur un
profil Playwright separe. Ici, c'est ton navigateur.

## Lancer « Chrome + Jarvis »

Double-clique **`Chrome + Jarvis.bat`** (a la racine du projet). Il ouvre Chrome
avec le port de debug que Jarvis utilise.

> **Pourquoi un profil dedie ?** Depuis Chrome 136 (tu as la 150), Google
> **interdit** le debug a distance sur le profil par defaut, pour la securite.
> Le raccourci ouvre donc un profil Chrome dedie « ChromeJarvis ». **Connecte-toi
> une fois** aux sites ou tu veux de l'aide (Google, YouTube...) : ca reste
> memorise. Tes mots de passe ne passent jamais par Jarvis.

Si tu demandes une action navigateur sans avoir lance ce raccourci, Jarvis
repond : « Chrome n'est pas connecte, lance-le via le raccourci Chrome + Jarvis. »

## Ce que tu peux dire

| Tu dis | Outil |
|---|---|
| « ouvre YouTube », « cherche des tests du Godox TL60 » | `browser_open` (nouvel onglet) |
| « resume cette page », « qu'est-ce qu'ils disent sur les prix », « traduis ca » | `browser_current_page` |
| « quels onglets sont ouverts », « passe sur l'onglet YouTube » | `browser_tabs` |
| « ferme cet onglet », « ferme tous les onglets YouTube » | `browser_close_tabs` (confirmation) |
| « accepte les cookies », « descends aux commentaires », « mets la video en pause » | `browser_interact` |

Exemple combine : « Jarvis, resume-moi cette page et note les points importants » ->
il lit l'onglet actif, te resume, et enregistre dans tes notes.

## Securite (ton Chrome = tes sessions connectees)

- **Domaines proteges** (`config.yaml -> navigateur.domaines_proteges` : banque,
  impots, sante...) : Jarvis peut **lire** la page si tu le demandes, mais **n'y
  fait aucune action** (clic, saisie). Il te dit de le faire toi-meme.
- **Jamais de mot de passe** saisi. Les champs mot de passe sont ignores.
- **Achat / paiement** : s'il detecte un achat, il s'arrete et te laisse valider.
- **Fermeture d'onglets** : action destructive -> il annonce et attend ton « oui »
  (« Je ferme 4 onglets YouTube, ok ? »). Les actions benignes (scroll, pause)
  sont directes.

## Configuration (`config.yaml`)

```yaml
navigateur:
  cdp_port: 9222
  domaines_proteges:
    - "impots.gouv.fr"
    - "ameli.fr"
    - "labanquepostale.fr"
    - "boursorama.com"
    # ajoute tes banques / sante / administration
```

Ces outils **ne sont pas exposes via MCP** (ils pilotent ton vrai navigateur).
