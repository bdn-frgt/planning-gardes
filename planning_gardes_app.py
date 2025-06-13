import streamlit as st
import pandas as pd
from datetime import timedelta
from collections import defaultdict
import io

# --- Configuration ---
REQUIRED_SHEETS = ["Dispo Période", "Pointage gardes", "Gardes résidents"]
OPTIONAL_SHEETS = ["Période précédente"]
REQUIRED_COLUMNS = {
    "Dispo Période": ["Jour", "Moment", "Date"],
    "Pointage gardes": ["MD", "Score actualisé"],
    "Gardes résidents": ["date", "Points"],
}
PREV_COLUMNS = {"Période précédente": ["Date", "Médecin"]}

# --- Validation ---
def validate_file(xls):
    errors = []
    for sheet, cols in REQUIRED_COLUMNS.items():
        if sheet not in xls.sheet_names:
            errors.append(f"Feuille manquante: {sheet}")
        else:
            df = xls.parse(sheet)
            for col in cols:
                if col not in df.columns:
                    errors.append(f"Colonne manquante dans {sheet}: {col}")
    if "Période précédente" in xls.sheet_names:
        df = xls.parse("Période précédente")
        for col in PREV_COLUMNS["Période précédente"]:
            if col not in df.columns:
                errors.append(f"Colonne manquante dans Période précédente: {col}")
    return errors

# --- Génération du planning et mise à jour du pointage ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None, seuil_proximite=6):
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    mask = ((dispo["Moment"].str.lower() == "soir") |
            (dispo["Jour"].str.lower().isin(["samedi", "dimanche"])))
    df_gardes = dispo[mask].copy()
    meds = [c for c in df_gardes.columns if c.startswith("Dr")]

    # Comptage dispo pour vérification
    for status in ["OUI", "PRN", "NON"]:
        df_gardes[f"nb_{status}"] = df_gardes[meds].apply(
            lambda r: sum(str(x).strip().upper() == status for x in r), axis=1)

    # Points par date
    gardes = gardes_df.copy()
    gardes["date"] = pd.to_datetime(gardes["date"])
    points_map = gardes.set_index("date")["Points"].to_dict()
    df_gardes["Points jour"] = df_gardes["Date"].map(points_map).fillna(0).astype(int)

    # Identifier groupes de week-end
    def weekend_id(date):
        wd = date.weekday()
        if wd == 4: return date
        if wd == 5: return date - timedelta(days=1)
        if wd == 6: return date - timedelta(days=2)
        return None
    df_gardes["weekend_id"] = df_gardes["Date"].apply(weekend_id)

    # Choisir le jour le plus difficile (max nb_NON)
    hardest = {}
    for wid, grp in df_gardes.groupby("weekend_id"):
        if pd.isna(wid): continue
        target = grp.sort_values(["nb_NON", "Date"], ascending=[False, True]).iloc[0]["Date"]
        hardest[wid] = target

    # Construire l'ordre d'attribution initial
    df_weekdays = df_gardes[df_gardes["weekend_id"].isna()]
    df_hardest = df_gardes[df_gardes["Date"].isin(hardest.values())]
    df_order = pd.concat([df_weekdays, df_hardest])
    df_order = df_order.sort_values(["nb_OUI", "nb_PRN", "Points jour"]).reset_index(drop=True)

    # Scores initiaux et historique
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    historique = defaultdict(list)
    if prev_df is not None:
        prev = prev_df.copy()
        prev["Date"] = pd.to_datetime(prev["Date"])
        for _, r in prev.iterrows():
            # inclure période précédente dans historique pour la règle de distance
            historique[r["Médecin"]].append(r["Date"])
    processed = set()
    logs = []
    assigns = []

    def est_proche(date, hist):
        return any(abs((date - d).days) < seuil_proximite for d in hist)

    # Attribution et log
    for _, row in df_order.iterrows():
        date, wid = row["Date"], row["weekend_id"]
        if pd.notna(wid) and (date != hardest[wid] or wid in processed):
            continue

        # Sélection pour week-end (3 OUI > 2+1 PRN > fallback)
        sel_nom, raison = None, None
        if pd.notna(wid):
            grp = df_gardes[df_gardes["weekend_id"] == wid]
            statuts = {m: grp[m].astype(str).str.upper().tolist() for m in meds}
            # 3 OUI
            c3 = [(m, scores[m]) for m, sts in statuts.items() if sts.count("OUI") == 3]
            if c3:
                sel_nom = min(c3, key=lambda x: x[1])[0]
                raison = "3 OUI week-end"
            else:
                # 2 OUI + 1 PRN
                c2 = [(m, scores[m]) for m, sts in statuts.items() 
                      if sts.count("OUI")==2 and sts.count("PRN")==1]
                if c2:
                    sel_nom = min(c2, key=lambda x: x[1])[0]
                    raison = "2 OUI + 1 PRN week-end"

        # Sélection standard si pas dans week-end
        sel_score_before = None
        if not sel_nom:
            meds_row = [(m, str(row[m]).upper(), scores.get(m,0), historique[m].copy()) 
                        for m in meds if str(row[m]).strip().upper() != "NON"]
            pool_oui = [c for c in meds_row if c[1] == "OUI"]
            pool_prn = [c for c in meds_row if c[1] == "PRN"]
            sel = None
            for pname, pool in [("OUI", pool_oui), ("PRN", pool_prn)]:
                for c in sorted(pool, key=lambda x: x[2]):
                    if not est_proche(date, c[3]):
                        sel = c; raison = f"Pool {pname}, score bas"
                        break
                if sel: break
            if not sel and meds_row:
                sel = min(meds_row, key=lambda x: x[2]); raison = "Score le plus faible"
            if sel:
                sel_nom, _, sel_score_before, _ = sel

        pts = row["Points jour"]
        prev_score = sel_score_before if sel_score_before is not None else scores.get(sel_nom,0)
        scores[sel_nom] = prev_score + pts
        historique[sel_nom].append(date)

        # Log attribution principale
        log = {"Date": date, "Médecin": sel_nom,
               "Points jour": pts, "Score avant": prev_score,
               "Score après": scores[sel_nom], "Raison": raison}
        logs.append(log); assigns.append(log.copy())

        # Groupement week-end
        if pd.notna(wid):
            for d in df_gardes[df_gardes["weekend_id"] == wid]["Date"]:
                if d == date: continue
                pts2 = int(df_gardes.loc[df_gardes["Date"] == d, "Points jour"].iloc[0])
                scores[sel_nom] += pts2; historique[sel_nom].append(d)
                lg = {"Date": d, "Médecin": sel_nom,
                      "Points jour": pts2, "Score avant": None,
                      "Score après": scores[sel_nom], "Raison": "Groupement week-end"}
                logs.append(lg); assigns.append(lg.copy())
            processed.add(wid)

    planning_df = pd.DataFrame(assigns)
    log_df = pd.DataFrame(logs)

        # Mise à jour du pointage : tenir compte des 11 anciennes + actuelle
    # Ancien score moyen sur 12 périodes
    old_scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    # Points de la période précédente
    prev_points = defaultdict(int)
    prev_presence = defaultdict(bool)
    if prev_df is not None:
        prev = prev_df.copy()
        prev["Date"] = pd.to_datetime(prev["Date"])
        for _, r in prev.iterrows():
            prev_points[r["Médecin"]] += points_map.get(r["Date"], 0)
            prev_presence[r["Médecin"]] = True
    # Points de la période actuelle
    current_points = planning_df.groupby("Médecin")["Points jour"].sum().to_dict()

    rows = []
    for md, old_avg in old_scores.items():
        # Somme des 12 anciennes périodes
        sum_old = old_avg * 12
        # Somme sur 11 anciennes (on retire la plus ancienne réelle)
        # Ici prev_points couvre la période précédente à retirer si présent
        sum_11 = sum_old - prev_points.get(md, 0)
        # Total incluant période actuelle
        total = sum_11 + current_points.get(md, 0)
        # Détermination du nombre de périodes de présence
        # 11 anciennes (toutes présentes sauf si absences), +1 actuelle
        # Si absent dans la période précédente, on a 11 présences max anciennes, sinon 12
        periods_present = 12 - (0 if prev_presence.get(md, False) else 1)
        # Nouveau score = total / périodes présentes
        new_avg = total / periods_present if periods_present > 0 else 0
        rows.append({
            "MD": md,
            "Ancien score": old_avg,
            "Points ancienne période": prev_points.get(md, 0),
            "Points actuelle période": current_points.get(md, 0),
            "Périodes considérées": periods_present,
            "Nouveau score": new_avg
        })
    new_pointage = pd.DataFrame(rows)

    return planning_df, log_df, new_pointage

# --- Interface utilisateur ---
... (rest unchanged)
