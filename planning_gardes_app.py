import streamlit as st
import pandas as pd
from datetime import timedelta
from collections import defaultdict
import io

# --- Configuration ---
REQUIRED_SHEETS = ["Dispo Période", "Pointage gardes", "Gardes résidents"]
REQUIRED_COLUMNS = {
    "Dispo Période": ["Jour", "Moment", "Date"],
    "Pointage gardes": ["MD", "Score actualisé"],
    "Gardes résidents": ["date", "Points"]
}

# --- Validation ---
def validate_file(xls):
    errors = []
    for sheet in REQUIRED_SHEETS:
        if sheet not in xls.sheet_names:
            errors.append(f"Feuille manquante: {sheet}")
        else:
            df = xls.parse(sheet)
            for col in REQUIRED_COLUMNS[sheet]:
                if col not in df.columns:
                    errors.append(f"Colonne manquante dans {sheet}: {col}")
    return errors

# --- Génération du planning avec log détaillé et groupement week-end ---
def generate_planning(dispo_df, pointage_df, gardes_df, seuil_proximite=6):
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    mask = ((dispo["Moment"].str.lower() == "soir") |
            (dispo["Jour"].str.lower().isin(["samedi", "dimanche"])))
    df_gardes = dispo[mask].copy()
    meds = [c for c in df_gardes.columns if c.startswith("Dr")]

    # Comptage disponibilités
    for status in ["OUI", "PRN", "NON"]:
        df_gardes[f"nb_{status}"] = df_gardes[meds].apply(
            lambda r: sum(str(x).strip().upper() == status for x in r), axis=1)

    # Points par date
    gardes = gardes_df.copy()
    gardes["date"] = pd.to_datetime(gardes["date"])
    points_map = gardes.set_index("date")["Points"].to_dict()
    df_gardes["Points"] = df_gardes["Date"].map(points_map).fillna(0).astype(int)

    # Identification des groupes de week-end
    def weekend_id(date):
        wd = date.weekday()
        if wd == 4: return date
        if wd == 5: return date - timedelta(days=1)
        if wd == 6: return date - timedelta(days=2)
        return None
    df_gardes["weekend_id"] = df_gardes["Date"].apply(weekend_id)

    # Sélection du jour "hardest" par week-end (max nb_NON)
    hardest = {}
    for wid, grp in df_gardes.groupby("weekend_id"):
        if pd.isna(wid): continue
        target = grp.sort_values(["nb_NON", "Date"], ascending=[False, True]).iloc[0]["Date"]
        hardest[wid] = target

    # Construction de l'ordre d'attribution initial
    df_weekdays = df_gardes[df_gardes["weekend_id"].isna()]
    df_hardest = df_gardes[~df_gardes["weekend_id"].isna()].loc[
        df_gardes["Date"].isin(hardest.values())
    ]
    df_order = pd.concat([df_weekdays, df_hardest]).sort_values(
        ["nb_OUI", "nb_PRN", "Points"]).reset_index(drop=True)

    # Initialisation scores et historique
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    historique = defaultdict(list)
    processed = set()
    logs = []
    assigns = []

    def est_proche(date, hist):
        return any(abs((date - d).days) < seuil_proximite for d in hist)

    # Attribution
    for _, row in df_order.iterrows():
        date = row["Date"]
        wid = row["weekend_id"]
        # Pour les week-ends, ne traiter que le "hardest"
        if pd.notna(wid) and (date != hardest[wid] or wid in processed):
            continue

        # Préparer candidats
        # Prendre statut pour chaque médecin pour ce week-end
        sel_nom = None
        raison = None
        if pd.notna(wid):
            grp = df_gardes[df_gardes["weekend_id"] == wid]
            # Statuts de disponibilité
            statuts = {
                m: grp[m].astype(str).str.strip().str.upper().tolist()
                for m in meds
            }
            # 1) 3 OUI
            candidats3 = [(m, scores[m]) for m, sts in statuts.items() if sts.count("OUI") == 3]
            if candidats3:
                sel_nom = min(candidats3, key=lambda x: x[1])[0]
                raison = "3 OUI week-end"
            else:
                # 2) 2 OUI + 1 PRN
                candidats2 = [
                    (m, scores[m]) for m, sts in statuts.items()
                    if sts.count("OUI") == 2 and sts.count("PRN") == 1
                ]
                if candidats2:
                    sel_nom = min(candidats2, key=lambda x: x[1])[0]
                    raison = "2 OUI + 1 PRN week-end"

        # Si pas de sélection week-end, logique normale
        sel_score_before = None
        if not sel_nom:
            meds_row = [
                (m, str(row[m]).upper(), scores.get(m, 0), historique[m].copy())
                for m in meds if str(row[m]).strip().upper() != "NON"
            ]
            pool_oui = [c for c in meds_row if c[1] == "OUI"]
            pool_prn = [c for c in meds_row if c[1] == "PRN"]
            sel = None
            for pool_name, pool in [("OUI", pool_oui), ("PRN", pool_prn)]:
                for cand in sorted(pool, key=lambda x: x[2]):
                    if not est_proche(date, cand[3]):
                        sel = cand
                        raison = f"Pool {pool_name}, score bas"
                        break
                if sel:
                    break
            if not sel and meds_row:
                sel = min(meds_row, key=lambda x: x[2])
                raison = "Score le plus faible"
            if sel:
                sel_nom, _, sel_score_before, _ = sel

        # Mise à jour du score
        pts = int(row["Points"])
        sel_score_before = sel_score_before if sel_score_before is not None else scores.get(sel_nom, 0)
        scores[sel_nom] = sel_score_before + pts
        historique[sel_nom].append(date)

        # Log de l'attribution pour le jour pivot
        log = {
            "Date": date,
            "Jour": row["Jour"],
            "Médecin": sel_nom,
            "Raison": raison,
            "Score avant": sel_score_before,
            "Score après": scores[sel_nom]
        }
        logs.append(log)
        assigns.append(log.copy())

        # Si week-end, attribuer les deux autres jours
        if pd.notna(wid):
            for d in df_gardes[df_gardes["weekend_id"] == wid]["Date"]:
                if d == date:
                    continue
                pts2 = int(df_gardes.loc[df_gardes["Date"] == d, "Points"].iloc[0])
                scores[sel_nom] += pts2
                historique[sel_nom].append(d)
                log2 = {
                    "Date": d,
                    "Jour": df_gardes.loc[df_gardes["Date"] == d, "Jour"].iloc[0],
                    "Médecin": sel_nom,
                    "Raison": "Groupement week-end",
                    "Score avant": None,
                    "Score après": scores[sel_nom]
                }
                logs.append(log2)
                assigns.append(log2.copy())
            processed.add(wid)

    return pd.DataFrame(assigns), pd.DataFrame(logs)

# --- Interface utilisateur ---
def main():
    st.title("Planning de gardes - 28 jours avec log détaillé")
    st.markdown(
        "Chargez un fichier Excel avec feuilles **Dispo Période**, **Pointage gardes**, **Gardes résidents**"
    )
    uploaded = st.file_uploader("Fichier Excel (.xlsx)", type=["xlsx"])
    seuil = st.number_input("Seuil de proximité (jours)", 1, 28, 6)
    if uploaded:
        xls = pd.ExcelFile(uploaded)
        errs = validate_file(xls)
        if errs:
            st.error("Erreurs de format:\n" + "\n".join(errs))
            return
        dispo = xls.parse("Dispo Période")
        pointage = xls.parse("Pointage gardes")
        gardes = xls.parse("Gardes résidents")

        planning, logs = generate_planning(dispo, pointage, gardes, seuil)

        st.subheader("Planning de gardes")
        st.dataframe(planning)
        buf1 = io.BytesIO()
        planning.to_excel(buf1, index=False, sheet_name="Planning")
        buf1.seek(0)
        st.download_button(
            "Télécharger planning (Excel)", buf1,
            "planning_gardes.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        st.subheader("Log détaillé d'attribution")
        st.dataframe(logs)
        buf2 = io.BytesIO()
        logs.to_excel(buf2, index=False, sheet_name="Log")
        buf2.seek(0)
        st.download_button(
            "Télécharger log (Excel)", buf2,
            "planning_gardes_log.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

if __name__ == "__main__":
    main()
