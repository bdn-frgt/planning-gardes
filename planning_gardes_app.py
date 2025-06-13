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
    # Préparation des données
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    mask = (dispo["Moment"].str.lower() == "soir") | (dispo["Jour"].str.lower().isin(["samedi", "dimanche"]))
    df_gardes = dispo[mask].copy()
    meds = [c for c in df_gardes.columns if c.startswith("Dr")]

    # Comptage disponibilités
    for status in ["OUI", "PRN", "NON"]:
        df_gardes[f"nb_{status}"] = df_gardes[meds].apply(lambda r: sum(str(x).strip().upper() == status for x in r), axis=1)

    # Points par date
    gardes = gardes_df.copy()
    gardes["date"] = pd.to_datetime(gardes["date"])
    points_map = gardes.set_index("date")["Points"].to_dict()
    df_gardes["Points"] = df_gardes["Date"].map(points_map).fillna(0).astype(int)

    # Définir groupe week-end (id = date du vendredi)
    def weekend_id(date):
        wd = date.weekday()
        if wd == 4:
            return date
        if wd == 5:
            return date - timedelta(days=1)
        if wd == 6:
            return date - timedelta(days=2)
        return None

    df_gardes["weekend_id"] = df_gardes["Date"].apply(weekend_id)

    # Calcul du jour le plus difficile (max nb_NON) pour chaque groupe
    hardest = {}
    for wid, grp in df_gardes.groupby("weekend_id"):
        if pd.isna(wid):
            continue
        days = grp[grp["weekend_id"] == wid]
        # choisir la date avec le plus de NON, en cas d'égalité, la plus petite date
        target = days.sort_values(["nb_NON", "Date"], ascending=[False, True]).iloc[0]["Date"]
        hardest[wid] = target

    # Ordre d'attribution initial (hors groupement)
    df_order = df_gardes[df_gardes["weekend_id"].isna()]
    df_weekend = df_gardes[~df_gardes["weekend_id"].isna()]
    df_order = pd.concat([
        df_order,
        df_weekend.loc[df_weekend.apply(lambda r: r["Date"] == hardest[r["weekend_id"]], axis=1)]
    ])
    df_order = df_order.sort_values(["nb_OUI", "nb_PRN", "Points"]).reset_index(drop=True)

    # Initialisations
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    historique = defaultdict(list)
    utilisés = set()
    processed_weekends = set()
    logs = []
    assignments = []

    def est_proche(date, dates_list):
        return any(abs((date - d).days) < seuil_proximite for d in dates_list)

    # Parcours et attribution
    for idx, row in df_order.iterrows():
        date = row["Date"]
        wid = row["weekend_id"]
        # si c'est un weekend et que ce n'est pas le jour le plus difficile, on skip
        if pd.notna(wid) and date != hardest[wid]:
            continue
        # si weekend déjà traitée, skip
        if pd.notna(wid) and wid in processed_weekends:
            continue

        # Cas d'une ligne à traiter (jour de semaine ou hardest du weekend)
        jour = row["Jour"].lower()
        pts = row["Points"]
        meds_row = [(m, str(row[m]).upper(), scores.get(m, 0), historique[m].copy()) for m in meds if str(row[m]).upper() != "NON"]

        log_entry = {
            "Date": date,
            "Jour": jour,
            "Points jour": pts,
            "nb_OUI": row["nb_OUI"],
            "nb_PRN": row["nb_PRN"],
            "Liste candidats": ", ".join([f"{m}({d},{scores[m]})" for m, d, _, _ in meds_row])
        }

        # Sélection candidat
        if not meds_row:
            sel_nom = "À assigner"
            raison = "Aucun disponible"
        else:
            pool_oui = [c for c in meds_row if c[1] == "OUI"]
            pool_prn = [c for c in meds_row if c[1] == "PRN"]
            sel = None
            for pool_name, pool in [("OUI", pool_oui), ("PRN", pool_prn)]:
                for cand in sorted(pool, key=lambda x: x[2]):
                    nom, dispo_status, score_av_before, hist = cand
                    if not est_proche(date, hist):
                        sel = cand
                        raison = f"Pool {pool_name}, score bas et pas de proximité"
                        break
                if sel:
                    break
            if not sel:
                sel = min(meds_row, key=lambda x: x[2])
                raison = "Score le plus faible parmi tous"
            sel_nom, sel_dispo, sel_score_av_before, _ = sel
            scores[sel_nom] += pts
            historique[sel_nom].append(date)
            sel_score_after = scores[sel_nom]
        # Log
        log_entry.update({
            "Sélection": sel_nom,
            "Disponibilité": sel_dispo if meds_row else None,
            "Score avant": sel_score_av_before if meds_row else None,
            "Score après": sel_score_after if meds_row else None,
            "Raison": raison
        })
        logs.append(log_entry)
        assignments.append({**log_entry, "Médecin": sel_nom})

        utilisés.add(date)

        # Si weekend, assigner les autres jours
        if pd.notna(wid):
            for other in df_gardes[df_gardes["weekend_id"] == wid]["Date"]:
                if other == date:
                    continue
                pts_w = df_gardes.loc[df_gardes["Date"] == other, "Points"].iloc[0]
                scores[sel_nom] += pts_w
                historique[sel_nom].append(other)
                used_j = {
                    "Date": other,
                    "Jour": df_gardes.loc[df_gardes["Date"] == other, "Jour"].iloc[0].lower(),
                    "Points jour": pts_w,
                    "nb_OUI": int(df_gardes.loc[df_gardes["Date"] == other, "nb_OUI"].iloc[0]),
                    "nb_PRN": int(df_gardes.loc[df_gardes["Date"] == other, "nb_PRN"].iloc[0]),
                    "Liste candidats": "(groupement week-end)",
                    "Sélection": sel_nom,
                    "Disponibilité": sel_dispo,
                    "Score avant": sel_score_after,
                    "Score après": scores[sel_nom],
                    "Raison": "Groupement week-end"
                }
                logs.append(used_j)
                assignments.append({**used_j, "Médecin": sel_nom})
                utilisés.add(other)
            processed_weekends.add(wid)

    return pd.DataFrame(assignments), pd.DataFrame(logs)

# --- Interface utilisateur ---

def main():
    st.title("Planning de gardes - 28 jours avec log détaillé")
    st.markdown(
        "Chargez un fichier Excel structuré avec les feuilles **Dispo Période**, **Pointage gardes**, et **Gardes résidents**."
    )
    uploaded = st.file_uploader("Sélectionnez votre fichier Excel (.xlsx)", type=["xlsx"])
    seuil = st.number_input("Seuil de proximité (jours)", min_value=1, max_value=28, value=6)

    if uploaded:
        xls = pd.ExcelFile(uploaded)
        errors = validate_file(xls)
        if errors:
            st.error("Erreurs de format :\n" + "\n".join(errors))
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
