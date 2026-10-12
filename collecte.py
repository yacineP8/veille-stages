"""Veille de stages « assistant chef de produit » en Île-de-France.

Interroge les sources listées dans config.json (API publiques des sites carrières),
garde les stages pertinents, repère la date de début et écrit offres.js pour index.html.

Usage : python collecte.py
"""
import concurrent.futures
import datetime
import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent
CONFIG = json.loads((RACINE / "config.json").read_text(encoding="utf-8"))
ETAT = RACINE / "donnees" / "offres.json"
MANUEL = RACINE / "manuel.json"
SORTIE_JS = RACINE / "offres.js"
AUJOURDHUI = datetime.date.today()
CIBLE = (CONFIG["debut_cible"]["annee"], CONFIG["debut_cible"]["mois"])
JOURS_CONSERVATION = 60
JOURS_NOUVEAU = 3

# ---------------------------------------------------------------- texte

def norm(texte):
    """Minuscules, sans accents, espaces et tirets normalisés."""
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c)).lower()
    texte = texte.replace("’", "'").replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", texte).strip()


def norm_titre(titre):
    """Comme norm, en retirant les marques d'écriture inclusive : assistant(e), chef.fe…"""
    t = norm(titre)
    t = re.sub(r"\((?:e|fe|ne|s|es|trice)\)", "", t)
    t = re.sub(r"[.·](?:e|fe|ne)\b", "", t)
    return re.sub(r"\s+", " ", t).strip()


def sans_html(source):
    texte = html.unescape(source or "")
    texte = re.sub(r"<(?:br|/p|/li|/div|/h\d)[^>]*>", "\n", texte, flags=re.I)
    texte = re.sub(r"<[^>]+>", " ", texte)
    return html.unescape(texte)

# ---------------------------------------------------------------- critères

ROLE_COEUR = re.compile(
    r"chef(?:fe)? de produits?|product manager|product marketing|marketing produits?"
    r"|brand manager|chef(?:fe)? de marque|marketing developpement|marketing development"
)
ROLE_ELARGI = re.compile(
    r"marketing|category manager|developpement produits?|product development|merchandising"
    r"|chef(?:fe)? de groupe|offre produits?"
    r"|chef(?:fe)? de projets?.*\b(?:produits?|innovation|offre|collections?)\b"
)
STAGE = re.compile(r"\b(?:stage|stagiaire|interns?|internship)\b")
STAGE_DESCRIPTION = re.compile(r"\bstagiaire\b|convention de stage|\bstage de \d|\binternship\b|\bstage\b.{0,40}\bmois\b")
EXCLU = re.compile(r"alternan|apprenti|work[- ]study|working student|\bcdi\b|\bcdd\b|\bv\.i\.e\b")

VILLES_IDF = (
    "paris|ile[- ]de[- ]france|idf|hauts[- ]de[- ]seine|seine[- ]saint[- ]denis|val[- ]de[- ]marne|val[- ]d'oise"
    "|yvelines|essonne|seine[- ]et[- ]marne|la defense|levallois|clichy|neuilly|boulogne|issy|courbevoie|puteaux"
    "|nanterre|saint[- ]ouen|saint[- ]denis|montreuil|pantin|asnieres|rueil|suresnes|massy|versailles|velizy"
    "|guyancourt|saint[- ]cloud|montrouge|ivry|vincennes|gennevilliers|colombes|roissy|cergy|evry|creteil"
    "|noisy|bobigny|aubervilliers|chatillon|malakoff|antony|marne[- ]la[- ]vallee|chessy|bussy|torcy|meudon"
    "|sevres|arcueil|gentilly|charenton|rungis|orly|palaiseau|saclay|les ulis|poissy|saint[- ]germain[- ]en[- ]laye"
    "|trappes|montigny|elancourt|plaisir|bezons|argenteuil|pontoise|bagneux|clamart|chatou|croissy|viroflay"
    "|fontenay|nogent|joinville|saint[- ]maur|villejuif|vitry|bagnolet|romainville|le plessis|chaville|garches"
    "|bois[- ]colombes|la garenne|villepinte|tremblay|lognes|noisiel|serris|melun|meaux|mantes|saint[- ]quentin[- ]en[- ]yvelines"
)
LIEU_IDF = re.compile(rf"\b(?:{VILLES_IDF})\b|\b(?:75|77|78|91|92|93|94|95)\d{{3}}\b")
LIEU_FLOU = re.compile(r"^$|^france$|locations?$|sites?$|emplacements?$|remote|teletravail")

MOIS = {
    "janvier": 1, "janv": 1, "jan": 1, "january": 1,
    "fevrier": 2, "fev": 2, "feb": 2, "february": 2,
    "mars": 3, "march": 3,
    "avril": 4, "avr": 4, "apr": 4, "april": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6,
    "juillet": 7, "juil": 7, "jul": 7, "july": 7,
    "aout": 8, "aug": 8, "august": 8,
    "septembre": 9, "sept": 9, "sep": 9, "september": 9,
    "octobre": 10, "oct": 10, "october": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "decembre": 12, "dec": 12, "december": 12,
}
NOMS_MOIS = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
             "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
_M = "|".join(sorted(MOIS, key=len, reverse=True))
DATE_MOIS = re.compile(rf"\b({_M})\.?(?:\s*(?:/|-|ou|or|et|and|a|to)\s*({_M})\.?)?\s*,?\s*(20[23]\d)\b")
DATE_CHIFFRES = re.compile(r"\b(?:[0-3]?\d[/.])?(0?[1-9]|1[0-2])[/.](20[23]\d)\b")
DEBUT_ANNEE = re.compile(
    rf"(?:debut|courant|premier semestre|1er semestre|premier trimestre|1er trimestre|\bs1|\bh1|\bq1|\bt1|printemps|spring)"
    rf"\s*(?:de |d')?(?:l'annee )?{CONFIG['debut_cible']['annee']}"
)
DUREE = re.compile(r"\b(\d{1,2})(?:\s*(?:a|-|/|to)\s*(\d{1,2}))?\s*(?:mois|months)\b")


def lieu_statut(lieu):
    n = norm(lieu)
    if LIEU_IDF.search(n):
        return "idf"
    if LIEU_FLOU.search(n):
        return "a_verifier"
    return "hors"


def dates_dans(texte):
    """Couples (année, mois) cités dans un texte normalisé."""
    trouvees = set()
    for m in DATE_MOIS.finditer(texte):
        annee = int(m.group(3))
        trouvees.add((annee, MOIS[m.group(1)]))
        if m.group(2):
            trouvees.add((annee, MOIS[m.group(2)]))
    for m in DATE_CHIFFRES.finditer(texte):
        trouvees.add((int(m.group(2)), int(m.group(1))))
    return trouvees


def analyse_debut(titre, description):
    """Classe la date de début : cible, proche (± 1 mois), autre, ou non précisée."""
    maintenant = (AUJOURDHUI.year, AUJOURDHUI.month)
    for texte, tout_garder in ((norm(titre), True), (norm(description), False)):
        # une date passée dans le titre signale une annonce périmée ; dans la description, c'est du bruit
        dates = {d for d in dates_dans(texte) if tout_garder or d >= maintenant}
        if CIBLE in dates:
            return {"statut": "cible", "label": f"{NOMS_MOIS[CIBLE[1]]} {CIBLE[0]}"}
        proches = sorted(d for d in dates if abs((d[0] - CIBLE[0]) * 12 + d[1] - CIBLE[1]) == 1)
        if proches:
            return {"statut": "proche", "label": f"{NOMS_MOIS[proches[0][1]]} {proches[0][0]}"}
        if DEBUT_ANNEE.search(texte):
            return {"statut": "proche", "label": f"Début {CIBLE[0]}"}
        if dates:
            premiere = min(dates)
            passe = " (passé)" if max(dates) < maintenant else ""
            return {"statut": "autre", "label": f"{NOMS_MOIS[premiere[1]]} {premiere[0]}{passe}"}
    return {"statut": "inconnue", "label": "Non précisée"}


def analyse_duree(titre, description):
    for texte in (norm(titre), norm(description)):
        m = DUREE.search(texte)
        if m and int(m.group(1)) <= 12:
            return f"{m.group(1)} à {m.group(2)} mois" if m.group(2) else f"{m.group(1)} mois"
    return ""


def pre_tri(titre, lieu):
    """Tri rapide sur le titre et le lieu, avant d'aller chercher le détail d'une offre."""
    t = norm_titre(titre)
    if EXCLU.search(t) or lieu_statut(lieu) == "hors":
        return None
    if ROLE_COEUR.search(t):
        return "coeur"
    if ROLE_ELARGI.search(t) and STAGE.search(t):
        return "elargi"
    return None


def qualifier(brute):
    """Transforme une offre brute en offre retenue, ou None si elle ne correspond pas."""
    t = norm_titre(brute["titre"])
    if EXCLU.search(t):
        return None
    role = "coeur" if ROLE_COEUR.search(t) else "elargi" if ROLE_ELARGI.search(t) else None
    if not role:
        return None
    description = brute.get("description", "")
    est_stage = bool(STAGE.search(t) or STAGE.search(norm(brute.get("contrat", ""))))
    if not est_stage and role == "coeur":
        est_stage = bool(STAGE_DESCRIPTION.search(norm(description)))
    if not est_stage:
        return None
    lieu = lieu_statut(brute.get("lieu", ""))
    if lieu == "hors":
        return None
    debut = analyse_debut(brute["titre"], description)
    score = (60 if role == "coeur" else 25) + (5 if lieu == "idf" else 0)
    score += {"cible": 40, "proche": 25, "inconnue": 10, "autre": 0}[debut["statut"]]
    return {
        "id": brute["id"],
        "titre": brute["titre"].strip(),
        "entreprise": brute["entreprise"],
        "lieu": brute.get("lieu", "").strip() or "À vérifier",
        "lieu_statut": lieu,
        "url": brute["url"],
        "source": brute["source"],
        "publie": (brute.get("publie") or "")[:10],
        "debut": debut,
        "duree": analyse_duree(brute["titre"], description),
        "role": role,
        "score": score,
    }

# ---------------------------------------------------------------- réseau

def http_json(url, corps=None):
    entetes = {
        "User-Agent": "Mozilla/5.0 (veille-stages, usage personnel)",
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }
    donnees = None
    if corps is not None:
        donnees = json.dumps(corps).encode("utf-8")
        entetes["Content-Type"] = "application/json"
    requete = urllib.request.Request(url, data=donnees, headers=entetes)
    with urllib.request.urlopen(requete, timeout=25) as reponse:
        return json.loads(reponse.read().decode("utf-8", errors="replace"))

# ---------------------------------------------------------------- sources

def source_workday(src):
    base = f"https://{src['tenant']}.{src['dc']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{src['tenant']}/{src['site']}"
    annonces = {}
    for requete in CONFIG["requetes_workday"]:
        for page in range(4):
            reponse = http_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": requete})
            lot = reponse.get("jobPostings") or []
            for annonce in lot:
                if annonce.get("externalPath"):
                    annonces.setdefault(annonce["externalPath"], annonce)
            if len(lot) < 20:
                break
    brutes = []
    for chemin, annonce in annonces.items():
        if not pre_tri(annonce.get("title", ""), annonce.get("locationsText", "")):
            continue
        try:
            info = http_json(api + chemin).get("jobPostingInfo", {})
        except (urllib.error.URLError, TimeoutError, ValueError):
            info = {}
        lieux = [info.get("location") or annonce.get("locationsText", "")] + (info.get("additionalLocations") or [])
        lieux = [l for l in lieux if l]
        en_idf = [l for l in lieux if lieu_statut(l) == "idf"]
        brutes.append({
            "id": f"workday:{src['tenant']}:{chemin.rsplit('/', 1)[-1]}",
            "titre": info.get("title") or annonce["title"],
            "entreprise": src["nom"],
            "lieu": (en_idf or lieux or [""])[0],
            "url": info.get("externalUrl") or f"{base}/{src['site']}{chemin}",
            "source": "Site carrières (Workday)",
            "publie": info.get("startDate", ""),
            "contrat": f"{info.get('timeType', '')} {info.get('jobFamilyGroup', '')}",
            "description": sans_html(info.get("jobDescription", "")),
        })
    return brutes


def source_smartrecruiters(src):
    api = f"https://api.smartrecruiters.com/v1/companies/{src['id']}/postings"
    annonces = {}
    for requete in CONFIG["requetes_smartrecruiters"]:
        for page in range(5):
            reponse = http_json(f"{api}?country=fr&limit=100&offset={page * 100}&q={requete}")
            lot = reponse.get("content") or []
            for annonce in lot:
                annonces.setdefault(annonce["id"], annonce)
            if len(lot) < 100:
                break
    brutes = []
    for identifiant, annonce in annonces.items():
        loc = annonce.get("location") or {}
        lieu = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region")) if x)
        if not pre_tri(annonce.get("name", ""), lieu):
            continue
        try:
            sections = (http_json(f"{api}/{identifiant}").get("jobAd") or {}).get("sections") or {}
        except (urllib.error.URLError, TimeoutError, ValueError):
            sections = {}
        description = "\n".join(sans_html((s or {}).get("text", "")) for s in sections.values())
        contrat = " ".join((annonce.get(cle) or {}).get("label", "") for cle in ("typeOfEmployment", "experienceLevel"))
        marque = (annonce.get("company") or {}).get("name") or src["nom"]
        brutes.append({
            "id": f"smartrecruiters:{src['id']}:{identifiant}",
            "titre": annonce["name"],
            "entreprise": src["nom"] if norm(src["nom"]) in norm(marque) else f"{src['nom']} – {marque}",
            "lieu": lieu,
            "url": f"https://jobs.smartrecruiters.com/{src['id']}/{identifiant}",
            "source": "Site carrières (SmartRecruiters)",
            "publie": annonce.get("releasedDate", ""),
            "contrat": contrat,
            "description": description,
        })
    return brutes


def source_greenhouse(src):
    reponse = http_json(f"https://boards-api.greenhouse.io/v1/boards/{src['id']}/jobs?content=true")
    return [{
        "id": f"greenhouse:{src['id']}:{annonce['id']}",
        "titre": annonce.get("title", ""),
        "entreprise": src["nom"],
        "lieu": (annonce.get("location") or {}).get("name", ""),
        "url": annonce.get("absolute_url", ""),
        "source": "Site carrières (Greenhouse)",
        "publie": annonce.get("first_published") or annonce.get("updated_at") or "",
        "description": sans_html(annonce.get("content", "")),
    } for annonce in reponse.get("jobs", [])]


def source_lever(src):
    brutes = []
    for annonce in http_json(f"https://api.lever.co/v0/postings/{src['id']}?mode=json"):
        categories = annonce.get("categories") or {}
        cree = annonce.get("createdAt")
        brutes.append({
            "id": f"lever:{src['id']}:{annonce['id']}",
            "titre": annonce.get("text", ""),
            "entreprise": src["nom"],
            "lieu": categories.get("location", ""),
            "url": annonce.get("hostedUrl", ""),
            "source": "Site carrières (Lever)",
            "publie": datetime.date.fromtimestamp(cree / 1000).isoformat() if cree else "",
            "contrat": categories.get("commitment", ""),
            "description": f"{annonce.get('descriptionPlain', '')}\n{annonce.get('additionalPlain', '')}",
        })
    return brutes


COLLECTEURS = {
    "workday": source_workday,
    "smartrecruiters": source_smartrecruiters,
    "greenhouse": source_greenhouse,
    "lever": source_lever,
}


def collecter(src):
    try:
        brutes = COLLECTEURS[src["type"]](src)
        retenues = [o for o in map(qualifier, brutes) if o]
        return src["nom"], retenues, None
    except Exception as erreur:  # une source en panne ne doit pas bloquer les autres
        return src["nom"], [], f"{type(erreur).__name__}: {erreur}"

# ---------------------------------------------------------------- programme

def offres_manuelles():
    """Offres ajoutées à la main dans manuel.json (trouvées sur Indeed, LinkedIn…)."""
    if not MANUEL.exists():
        return []
    retenues = []
    for brute in json.loads(MANUEL.read_text(encoding="utf-8")):
        offre = qualifier(brute)
        if offre:
            if brute.get("debut"):
                offre["debut"] = brute["debut"]
            retenues.append(offre)
    return retenues


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    precedent = json.loads(ETAT.read_text(encoding="utf-8")) if ETAT.exists() else None
    connues = {o["id"]: o for o in (precedent or {}).get("offres", [])}
    jour = AUJOURDHUI.isoformat()
    # « nouveau » = apparue depuis moins de JOURS_NOUVEAU jours, hors tout premier chargement
    debut_veille = (precedent or {}).get("debut_veille") or min((o["premiere_vue"] for o in connues.values()), default=jour)
    seuil_nouveau = (AUJOURDHUI - datetime.timedelta(days=JOURS_NOUVEAU - 1)).isoformat()

    trouvees, erreurs = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executeur:
        for nom, offres, erreur in executeur.map(collecter, CONFIG["sources"]):
            print(f"  {nom:<40} {'ERREUR ' + erreur if erreur else str(len(offres)) + ' offre(s)'}")
            if erreur:
                erreurs.append({"source": nom, "erreur": erreur})
            for offre in offres:
                trouvees[offre["id"]] = offre
    for offre in offres_manuelles():
        trouvees[offre["id"]] = offre

    sources_en_erreur = {e["source"] for e in erreurs}
    resultat = []
    for identifiant, offre in trouvees.items():
        ancienne = connues.get(identifiant)
        offre["premiere_vue"] = ancienne["premiere_vue"] if ancienne else jour
        offre["derniere_vue"] = jour
        offre["active"] = True
        offre["nouveau"] = offre["premiere_vue"] > debut_veille and offre["premiere_vue"] >= seuil_nouveau
        resultat.append(offre)
    limite = (AUJOURDHUI - datetime.timedelta(days=JOURS_CONSERVATION)).isoformat()
    for identifiant, ancienne in connues.items():
        if identifiant in trouvees or ancienne["derniere_vue"] < limite:
            continue
        # offre disparue : gardée un temps pour le suivi, sauf si sa source était juste en panne
        en_panne = any(ancienne["entreprise"].startswith(s) for s in sources_en_erreur)
        ancienne["active"] = en_panne and ancienne.get("active", True)
        ancienne["nouveau"] = False
        if en_panne:
            ancienne["derniere_vue"] = jour
        resultat.append(ancienne)

    resultat.sort(key=lambda o: o["publie"], reverse=True)
    resultat.sort(key=lambda o: o["score"], reverse=True)
    donnees = {
        "maj": datetime.datetime.now().astimezone().isoformat(timespec="minutes"),
        "debut_veille": debut_veille,
        "cible": f"{NOMS_MOIS[CIBLE[1]]} {CIBLE[0]}",
        "sources": [s["nom"] for s in CONFIG["sources"]],
        "erreurs": erreurs,
        "offres": resultat,
    }
    ETAT.parent.mkdir(exist_ok=True)
    ETAT.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    SORTIE_JS.write_text("window.VEILLE = " + json.dumps(donnees, ensure_ascii=False) + ";\n", encoding="utf-8")

    actives = [o for o in resultat if o["active"]]
    par_debut = {s: sum(1 for o in actives if o["debut"]["statut"] == s) for s in ("cible", "proche", "inconnue", "autre")}
    print(f"\n{len(actives)} offre(s) retenue(s), dont {sum(o['nouveau'] for o in actives)} nouvelle(s).")
    print(f"Début {donnees['cible']} : {par_debut['cible']} | proche : {par_debut['proche']} | "
          f"non précisé : {par_debut['inconnue']} | autre date : {par_debut['autre']}")
    if erreurs:
        print(f"{len(erreurs)} source(s) en erreur (voir ci-dessus).")


if __name__ == "__main__":
    main()
