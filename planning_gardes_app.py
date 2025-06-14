import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from collections import defaultdict
import io

# --- Configuration ---
# Liste des médecins - copiez-collez vos noms EXACTEMENT entre guillemets, séparés par des virgules
# Exemple : DOCTORS = ["DrAlice", "DrBob", "DrCharlie"]
DOCTORS = [
    "DrAlice", 
    "DrBob", 
    "DrCharlie",
    # Ajoutez ici tous les médecins de l'équipe
]

# Colonnes attendues dans chaque onglet
REQUIRED_SHEETS = ["Dispo Période", "Pointage gardes", "Gardes résidents"]
OPTIONAL_SHEETS = ["Période précédente"]
REQUIRED_COLUMNS = {
    "Dispo Période": ["Jour", "Moment", "Date"] + DOCTORS,
    "Pointage gardes": ["MD", "Score actualisé"],
    "Gardes résidents": ["date", "Points"],
}
PREV_COLUMNS = {"Période précédente": ["Date", "Médecin"]}

# --- Validation du fichier Excel importé ---
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

# --- Génération du template Excel ---
def create_template_excel(start_date: date,
                          num_weeks: int,
                          periods_ante: int,
                          pts_sem_res: int,
                          pts_sem_nores: int,
                          pts_we_res: int,
                          pts_we_nores: int) -> io.BytesIO:
    docs = DOCTORS.copy()
    total_days = num_weeks * 7
    dates = [start_date + timedelta(days=i) for i in range(total_days)]
    # Dispo Période
    dispo_rows = []
    for d in dates:
        jour = d.strftime("%A")
        moment = "Soir" if d.weekday() < 5 else ""
        dispo_rows.append({"Jour": jour, "Moment": moment, "Date": d})
    dispo_df = pd.DataFrame(dispo_rows)
    for m in docs:
        dispo_df[m] = "PRN"
    # Pointage gardes
    pt_df = pd.DataFrame({"MD": docs, "Score actualisé": [0] * len(docs)})
    # Gardes résidents (vide)
    gard_res_df = pd.DataFrame(columns=["date", "Points"])
    # Période précédente (vide)
    prev_df = pd.DataFrame(columns=["Date", "Médecin"])
    # Paramètres
    params_df = pd.DataFrame({
        "Paramètre": ["periods_ante","pts_sem_res","pts_sem_nores","pts_we_res","pts_we_nores"],
        "Valeur": [periods_ante,pts_sem_res,pts_sem_nores,pts_we_res,pts_we_nores]
    })
    # Écriture Excel en mémoire
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dispo_df.to_excel(writer, sheet_name="Dispo Période", index=False)
        pt_df.to_excel(writer, sheet_name="Pointage gardes", index=False)
        gard_res_df.to_excel(writer, sheet_name="Gardes résidents", index=False)
        prev_df.to_excel(writer, sheet_name="Période précédente", index=False)
        params_df.to_excel(writer, sheet_name="Paramètres", index=False)
    output.seek(0)
    return output

# --- Implémentation complète de generate_planning ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None,
                      seuil_proximite=6, max_weekends=1, bonus_oui=5):
    # Préparation des données
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    meds = DOCTORS
    # Filtrer soirées + week-ends
    mask = ((dispo["Moment"].str.lower() == "soir") |
            (dispo["Jour"].str.lower().isin(["samedi", "dimanche"])))
    df_g = dispo[mask].copy()
    # Points par date
    grd = gardes_df.copy(); grd["date"] = pd.to_datetime(grd["date"])
    points_map = grd.set_index("date")["Points"].to_dict()
    df_g["Points jour"] = df_g["Date"].map(points_map).fillna(0).astype(int)
    # Comptages
    for s in ["OUI","PRN","NON"]:
        df_g[f"nb_{s}"] = df_g[meds].apply(lambda r: sum(str(x).strip().upper()==s for x in r), axis=1)
    # week-end id
    def weekend_id(d): wd=d.weekday(); return d if wd==4 else (d-timedelta(days=1) if wd==5 else (d-timedelta(days=2) if wd==6 else None))
    df_g["we_id"] = df_g["Date"].apply(weekend_id)
    # Init scores,historique,count
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    history = defaultdict(list)
    we_count = defaultdict(int)
    if prev_df is not None:
        prev_df["Date"] = pd.to_datetime(prev_df["Date"])
        for _,r in prev_df.iterrows():
            history[r["Médecin"]].append(r["Date"])
            wid = weekend_id(r["Date"])
            if wid: we_count[r["Médecin"]]+=1
    plans=[]; logs=[]
    # Traiter chaque week-end
    for wid, grp in df_g[df_g["we_id"].notna()].groupby("we_id"):
        dates=sorted(grp["Date"])
        # candidats respectant cap
        cands=[]
        for m in meds:
            if we_count[m]>=max_weekends: continue
            stats=[str(grp.loc[grp["Date"]==d,m].iloc[0]).strip().upper() for d in dates]
            if stats.count("NON")==len(stats): continue
            base=scores[m]; bonus=stats.count("OUI")*bonus_oui
            cands.append((m,base-bonus,stats))
        if not cands: continue
        sel=min(cands,key=lambda x:x[1])[0]
        we_count[sel]+=1
        for d in dates:
            disp=str(df_g.loc[df_g["Date"]==d,sel].iloc[0]).strip().upper()
            prev_sc=scores[sel]; pts=points_map.get(d,0)
            scores[sel]=prev_sc+pts; history[sel].append(d)
            rec={"Date":d,"Médecin":sel,"Statut":disp,
                 "nb_OUI":int(df_g.loc[df_g["Date"]==d,"nb_OUI"].iloc[0]),
                 "nb_PRN":int(df_g.loc[df_g["Date"]==d,"nb_PRN"].iloc[0]),
                 "Points jour":pts,"Score avant":prev_sc,
                 "Score après":scores[sel],"Type":"WE"}
            plans.append(rec); logs.append(rec.copy())
    # Jours simples
    simple=df_g[df_g["we_id"].isna()].sort_values(["nb_OUI","nb_PRN","Points jour"])
    for _,row in simple.iterrows():
        d=row["Date"]
        if any(p["Date"]=d for p in plans): continue
        cands=[]
        for m in meds:
            disp=str(row[m]).strip().upper()
            if disp=="NON": continue
            if any(abs((d-x).days)<seuil_proximite for x in history[m]): continue
            base=scores[m]; bonus=bonus_oui if disp=="OUI" else 0
            cands.append((m,disp,base-bonus))
        if cands:
            m_sel,disp_sel,_=min(cands,key=lambda x:x[2])
        else:
            m_sel,disp_sel=None,None
        prev_sc=scores.get(m_sel,0) if m_sel else None
        pts=row["Points jour"]
        if m_sel: scores[m_sel]=prev_sc+pts; history[m_sel].append(d)
        rec={"Date":d,"Médecin":m_sel,"Statut":disp_sel,
             "nb_OUI":int(row["nb_OUI"]),"nb_PRN":int(row["nb_PRN"]),
             "Points jour":pts,"Score avant":prev_sc,
             "Score après":scores.get(m_sel),"Type":"Simple"}
        plans.append(rec); logs.append(rec.copy())
    planning_df=pd.DataFrame(plans).sort_values("Date").reset_index(drop=True)
    log_df=pd.DataFrame(logs)
    # Pointage mis à jour (pas modifié ici)
    pointage_update_df=pointage_df.copy()
    return planning_df, log_df, pointage_update_df

# --- Guides et UI ---
def make_guide_planner():
    md=("# Guide gestionnaire\n1. Mettre à jour DOCTORS...")
    return md.encode('utf-8')

def make_guide_physician():
    md=("# Guide médecin\n- OUI, PRN, NON...")
    return md.encode('utf-8')

def main():
    st.title("Planning de gardes optimisé")
    # ... reste de l'UI inchangé ...

if __name__=="__main__": main()
