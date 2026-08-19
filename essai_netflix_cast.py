"""Netflix accepte-t-il d'ouvrir une session de diffusion ?

L'API historique repond « disponible », mais disponible ne veut pas dire
utilisee : Chrome injecte ce script sur toutes les pages. Ce qui tranche, c'est
de demander la session. Si Netflix a declare un recepteur, elle s'ouvre ; s'il
n'a rien declare, l'erreur le dira sans ambiguite.

On designe l'ecran a l'avance, comme pour CANAL, pour qu'aucun selecteur ne
vienne attendre un clic.
"""
import sys
import time

sys.path.insert(0, ".")

from tools.navigateur_cast import _brancher, _sinks  # noqa: E402

ECRAN = sys.argv[1] if len(sys.argv) > 1 else "Tv en bas"
Q = chr(39)

DEMANDE = (
    "new Promise(res => {"
    "  try {"
    "    if (!window.chrome || !chrome.cast || !chrome.cast.requestSession) {"
    "      return res('pas d API');"
    "    }"
    "    chrome.cast.requestSession("
    "      s => res('session:' + (s.receiver ? s.receiver.friendlyName : '?')"
    "               + ' app:' + (s.appId || '?')),"
    "      e => res('erreur:' + (e && (e.code || e.description) || e))"
    "    );"
    "    setTimeout(() => res('sans reponse'), 25000);"
    "  } catch (e) { res('exception:' + e.message); }"
    "})"
).replace("'", Q)


def main():
    cdp = _brancher("netflix")
    if cdp is None:
        print("pas de connexion")
        return

    etat = cdp.evaluer(
        "JSON.stringify({chemin: location.pathname, joue: (()=>{"
        "const v=document.getElementsByTagName('video')[0];"
        "return v ? !v.paused : false;})()})".replace("'", Q), attente=15)
    print("page :", etat)

    vus = _sinks(cdp)
    print("ecrans :", [n for n, _ in vus])
    cdp.demander("Cast.setSinkToUse", {"sinkName": ECRAN})
    time.sleep(1)

    print("demande de session...")
    print("resultat :", cdp.evaluer(DEMANDE, geste=True, attente=45))

    cdp.fermer()


if __name__ == "__main__":
    main()
