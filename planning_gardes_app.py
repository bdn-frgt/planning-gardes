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

# --- Génération planning + global weekend grouping + rebalance ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None,
                      seuil_proximite=6, max_weekends=1, bonus_oui=5):
    # Préparation
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    mask = ((dispo["Moment"].str.lower() == "soir") |
            (dispo["Jour"].str.lower().isin(["samedi", "dimanche"])))
    df_g = dispo[mask].copy()
    meds = [c for c in df_g.columns if c.startswith("Dr")]
    # Points jour
    grd = gardes_df.copy()
    grd["date"] = pd.to_datetime(grd["date"])
    points_map = grd.set_index("date")["Points"].to_dict()
    df_g["Points jour"] = df_g["Date"].map(points_map).fillna(0).astype(int)
    # Comptage
    for s in ["OUI","PRN","NON"]:
        df_g[f"nb_{s}"] = df_g[meds].apply(lambda r: sum(str(x).strip().upper()==s for x in r), axis=1)
    # weekend_id
    def weekend_id(d):
        wd = d.weekday()
        if wd == 4: return d
        if wd == 5: return d - timedelta(days=1)
        if wd == 6: return d - timedelta(days=2)
        return None
    df_g["weekend_id"] = df_g["Date"].apply(weekend_id)
    # Initialisation
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    history = defaultdict(list)
    weekend_count = defaultdict(int)
    # Charger période précédente
    if prev_df is not None:
        prev = prev_df.copy()
        prev["Date"] = pd.to_datetime(prev["Date"])
        for _, r in prev.iterrows():
            history[r["Médecin"]].append(r["Date"])
            wid = weekend_id(r["Date"])
            if wid is not None:
                weekend_count[r["Médecin"]] += 1
    # Séparer simples et groupes
    df_simple = df_g[df_g["weekend_id"].isna()].sort_values(["nb_OUI","nb_PRN","Points jour"])
    df_group = df_g[df_g["weekend_id"].notna()].copy()
    # Traitement des week-ends global
    weekend_plans = []
    for wid_val, grp in df_group.groupby("weekend_id"):
        dates = sorted(grp["Date"])
        # Candidats respectant cap
        candidates = []
        for m in meds:
            if weekend_count.get(m,0) >= max_weekends: continue
            stats = [str(grp.loc[grp["Date"]==d, m].values[0]).strip().upper() for d in dates]
            if stats.count("NON") == len(stats): continue
            base = scores.get(m,0)
            bonus = stats.count("OUI") * bonus_oui
            candidates.append((m, base - bonus))
        if not candidates: continue
        sel = min(candidates, key=lambda x: x[1])[0]
        weekend_count[sel] += 1
        for d in dates:
            scores[sel] += points_map.get(d, 0)
            history[sel].append(d)
            weekend_plans.append({"Date": d, "Médecin": sel, "Type": "WE"})
    # Traitement des jours simples
    simple_plans = []
    for _, row in df_simple.iterrows():
        d = row["Date"]
        if any(wp["Date"] == d for wp in weekend_plans): continue
        # Candidats OUI/PRN
        cands = []
        for m in meds:
            disp = str(row[m]).strip().upper()
            if disp == "NON": continue
            if any(abs((d - x).days) < seuil_proximite for x in history[m]): continue
            base = scores.get(m,0)
            bonus = bonus_oui if disp == "OUI" else 0
            cands.append((m, disp, base - bonus))
        if not cands:
            sel = (None, None)
        else:
            sel = min(cands, key=lambda x: x[2])[0:2]
        m_sel, stat = sel if sel else (None, None)
        scores[m_sel] = scores.get(m_sel,0) + row["Points jour"] if m_sel else scores.get(m_sel,0)
        history[m_sel].append(d) if m_sel else None
        simple_plans.append({"Date": d, "Médecin": m_sel, "Type": stat})
    # Combiner et rebalance
    plans = pd.DataFrame(simple_plans + weekend_plans).sort_values("Date")
    # TODO: rebalance post-process pour échanger NON vs OUI quand possible
    return plans, pd.DataFrame(), None

# --- Interface utilisateur ---
def main():
    st.title("Planning gardes optimisé")
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
        plan, _, _ = generate_planning(dispo, pointage, gardes, prev, seuil, max_we, bonus)
        st.dataframe(plan)

if __name__ == "__main__":
    main()
