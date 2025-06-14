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
        df_prev = xls.parse("Période précédente")
        for col in PREV_COLUMNS["Période précédente"]:
            if col not in df_prev.columns:
                errors.append(f"Colonne manquante dans Période précédente: {col}")
    return errors

# --- Génération planning + logs + pointage mis à jour ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None,
                      seuil_proximite=6, max_weekends=1, bonus_oui=5):
    # Préparation
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    meds = [c for c in dispo.columns if c.startswith("Dr")]

    # Filtrer les jours de garde
    mask = ((dispo["Moment"].str.lower() == "soir") |
            (dispo["Jour"].str.lower().isin(["samedi", "dimanche"])))
    df_g = dispo[mask].copy()

    # Points par date
    grd = gardes_df.copy(); grd["date"] = pd.to_datetime(grd["date"])
    points_map = grd.set_index("date")["Points"].to_dict()
    df_g["Points jour"] = df_g["Date"].map(points_map).fillna(0).astype(int)

    # Comptage dispo
    for s in ["OUI", "PRN", "NON"]:
        df_g[f"nb_{s}"] = df_g[meds].apply(lambda r: sum(str(x).strip().upper()==s for x in r), axis=1)

    # Identifiant weekend
    def weekend_id(d):
        wd = d.weekday()
        if wd == 4: return d
        if wd == 5: return d - timedelta(days=1)
        if wd == 6: return d - timedelta(days=2)
        return None
    df_g["weekend_id"] = df_g["Date"].apply(weekend_id)

    # Initialiser scores, historique et compteur week-ends
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    history = defaultdict(list)
    weekend_count = defaultdict(int)
    if prev_df is not None:
        prev = prev_df.copy(); prev["Date"] = pd.to_datetime(prev["Date"])
        for _, r in prev.iterrows():
            history[r["Médecin"]].append(r["Date"])
            wid = weekend_id(r["Date"])
            if wid is not None:
                weekend_count[r["Médecin"]] += 1

    # Préparer structures de résultats
    plans = []
    logs = []

    # Regrouper week-ends
    for wid_val, grp in df_g[df_g["weekend_id"].notna()].groupby("weekend_id"):
        dates = sorted(grp["Date"])
        # sélectionner candidats
        cands = []
        for m in meds:
            if weekend_count.get(m,0) >= max_weekends: continue
            stats = [str(grp.loc[grp["Date"]==d, m].iloc[0]).strip().upper() for d in dates]
            if stats.count("NON") == len(stats): continue
            base = scores.get(m,0)
            bonus = stats.count("OUI") * bonus_oui
            cands.append((m, base - bonus, stats))
        if not cands: continue
        sel = min(cands, key=lambda x: x[1])[0]
        # assigner
        weekend_count[sel] += 1
        for d in dates:
            disp = str(df_g.loc[df_g["Date"]==d, sel].iloc[0]).strip().upper()
            prev_score = scores.get(sel,0)
            pts = points_map.get(d,0)
            scores[sel] = prev_score + pts
            history[sel].append(d)
            plans.append({
                "Date": d,
                "Médecin": sel,
                "Statut": disp,
                "nb_OUI": int(df_g.loc[df_g["Date"]==d, "nb_OUI"].iloc[0]),
                "nb_PRN": int(df_g.loc[df_g["Date"]==d, "nb_PRN"].iloc[0]),
                "Points jour": pts,
                "Score avant": prev_score,
                "Score après": scores[sel],
                "Type": "WE"
            })
            logs.append(plans[-1].copy())

    # Attribution des jours simples
    simple = df_g[df_g["weekend_id"].isna()].sort_values(["nb_OUI","nb_PRN","Points jour"])
    for _, row in simple.iterrows():
        d = row["Date"]
        if any(p["Date"] == d for p in plans): continue
        # candidats OUI/PRN
        cands = []
        for m in meds:
            disp = str(row[m]).strip().upper()
            if disp == "NON": continue
            if any(abs((d - x).days) < seuil_proximite for x in history[m]): continue
            base = scores.get(m,0)
            bonus = bonus_oui if disp == "OUI" else 0
            cands.append((m, disp, base - bonus))
        if cands:
            sel_m, sel_disp, _ = min(cands, key=lambda x: x[2])
        else:
            sel_m, sel_disp = None, None
        prev_score = scores.get(sel_m,0) if sel_m else None
        pts = row["Points jour"]
        if sel_m:
            scores[sel_m] = prev_score + pts
            history[sel_m].append(d)
        record = {
            "Date": d,
            "Médecin": sel_m,
            "Statut": sel_disp,
            "nb_OUI": int(row["nb_OUI"]),
            "nb_PRN": int(row["nb_PRN"]),
            "Points jour": pts,
            "Score avant": prev_score,
            "Score après": scores.get(sel_m),
            "Type": "Simple"
        }
        plans.append(record)
        logs.append(record.copy())

    planning_df = pd.DataFrame(plans).sort_values("Date").reset_index(drop=True)
    log_df = pd.DataFrame(logs)

    # Mise à jour du pointage (inchangé ici) : placeholder
    pointage_update_df = pointage_df.copy()

    return planning_df, log_df, pointage_update_df

# --- Interface utilisateur ---
def main():
    st.title("Planning de gardes optimisé")
    st.sidebar.header("Paramètres")
    seuil = st.sidebar.number_input("Seuil proximité (jours)", 1, 28, 6)
    max_we = st.sidebar.number_input("Max week-ends par médecin", 0, 52, 1)
    bonus = st.sidebar.number_input("Bonus pour un OUI", 0, 100, 5)
    uploaded = st.file_uploader("Fichier Excel (.xlsx)", type=["xlsx"])
    if uploaded:
        xls = pd.ExcelFile(uploaded)
        errs = validate_file(xls)
        if errs:
            st.error("Erreurs:\n" + "\n".join(errs))
            return
        dispo = xls.parse("Dispo Période")
        pointage = xls.parse("Pointage gardes")
        gardes = xls.parse("Gardes résidents")
        prev = xls.parse("Période précédente") if "Période précédente" in xls.sheet_names else None

        planning_df, log_df, pointage_update_df = generate_planning(
            dispo, pointage, gardes, prev, seuil, max_we, bonus)

        st.subheader("Planning de gardes (28 jours)")
        st.dataframe(planning_df)
        buf1 = io.BytesIO(); planning_df.to_excel(buf1, index=False); buf1.seek(0)
        st.download_button("Télécharger planning", buf1, "planning_gardes.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.subheader("Log détaillé")
        st.dataframe(log_df)
        buf2 = io.BytesIO(); log_df.to_excel(buf2, index=False); buf2.seek(0)
        st.download_button("Télécharger log", buf2, "planning_gardes_log.xlsx",
                           "application/vnd.openxmlformats-officedocument-spreadsheetml.sheet")

        st.subheader("Pointage mis à jour")
        st.dataframe(pointage_update_df)
        buf3 = io.BytesIO(); pointage_update_df.to_excel(buf3, index=False); buf3.seek(0)
        st.download_button("Télécharger pointage mis à jour", buf3,
                           "pointage_gardes.xlsx",
                           "application/vnd.openxmlformats-officedocument-spreadsheetml.sheet")

if __name__ == "__main__":
    main()
