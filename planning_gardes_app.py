import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from collections import defaultdict
import io
from reportlab.pdfgen import canvas as pdf_canvas

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

# --- Création du template Excel ---
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
    # Gardes résidents (prérempli dates)
    gard_res_df = pd.DataFrame({"date": dates, "Points": ["" for _ in dates]})
    # Période précédente (vide)
    prev_df = pd.DataFrame(columns=["Date", "Médecin"])
    # Paramètres
    params_df = pd.DataFrame({
        "Paramètre": ["periods_ante","pts_sem_res","pts_sem_nores","pts_we_res","pts_we_nores"],
        "Valeur": [periods_ante, pts_sem_res, pts_sem_nores, pts_we_res, pts_we_nores]
    })
    # Écriture Excel en mémoire avec validation
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        # Write sheets
        dispo_df.to_excel(writer, sheet_name="Dispo Période", index=False)
        pt_df.to_excel(writer, sheet_name="Pointage gardes", index=False)
        gard_res_df.to_excel(writer, sheet_name="Gardes résidents", index=False)
        prev_df.to_excel(writer, sheet_name="Période précédente", index=False)
        params_df.to_excel(writer, sheet_name="Paramètres", index=False)
        # Add data validation for OUI/PRN/NON in Dispo Période
        workbook  = writer.book
        worksheet = writer.sheets["Dispo Période"]
        # Determine the range rows: from row 2 to row total_days+1
        first_row = 1  # zero-indexed, header at row 0
        last_row = total_days  # zero-indexed includes header shift
        # Columns: after 'Date', doctors start at index 3
        for idx, doc in enumerate(docs, start=3):
            col_letter = chr(ord('A') + idx)
            worksheet.data_validation(
                f"{col_letter}2:{col_letter}{total_days+1}",
                {
                    'validate': 'list',
                    'source': ['OUI', 'PRN', 'NON'],
                    'input_title': 'Disponibilité',
                    'input_message': 'Choisissez OUI, PRN ou NON'
                }
            )
    output.seek(0)
    return output

# --- Implémentation complète de generate_planning ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None,
                      seuil_proximite=6, max_weekends=1, bonus_oui=5):
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    meds = DOCTORS
    mask = ((dispo["Moment"].str.lower() == "soir") |
            (dispo["Jour"].str.lower().isin(["samedi", "dimanche"])))
    df_g = dispo[mask].copy()
    grd = gardes_df.copy(); grd["date"] = pd.to_datetime(grd["date"])
    points_map = grd.set_index("date")["Points"].to_dict()
    df_g["Points jour"] = df_g["Date"].map(points_map).fillna(0).astype(int)
    for s in ["OUI","PRN","NON"]:
        df_g[f"nb_{s}"] = df_g[meds].apply(lambda r: sum(str(x).strip().upper()==s for x in r), axis=1)
    def weekend_id(d): wd=d.weekday(); return d if wd==4 else (d-timedelta(days=1) if wd==5 else (d-timedelta(days=2) if wd==6 else None))
    df_g["we_id"] = df_g["Date"].apply(weekend_id)
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
    # Week-ends
    for wid, grp in df_g[df_g["we_id"].notna()].groupby("we_id"):
        dates=sorted(grp["Date"])
        cands=[]
        for m in meds:
            if we_count[m]>=max_weekends: continue
            stats=[str(grp.loc[grp["Date"]==d,m].iloc[0]).strip().upper() for d in dates]
            if stats.count("NON")==len(stats): continue
            base=scores[m]; bonus=stats.count("OUI")*bonus_oui
            cands.append((m,base-bonus,stats))
        if not cands: continue
        sel=min(cands,key=lambda x:x[1])[0]; we_count[sel]+=1
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
        if any(p["Date"]==d for p in plans): continue
        cands=[]
        for m in meds:
            disp=str(row[m]).strip().upper();
            if disp=="NON": continue
            if any(abs((d-x).days)<seuil_proximite for x in history[m]): continue
            base=scores[m]; bonus=bonus_oui if disp=="OUI" else 0
            cands.append((m,disp,base-bonus))
        if cands: m_sel,disp_sel,_=min(cands,key=lambda x:x[2])
        else: m_sel,disp_sel=None,None
        prev_sc=scores.get(m_sel,0) if m_sel else None; pts=row["Points jour"]
        if m_sel: scores[m_sel]=prev_sc+pts; history[m_sel].append(d)
        rec={"Date":d,"Médecin":m_sel,"Statut":disp_sel,
             "nb_OUI":int(row["nb_OUI"]),"nb_PRN":int(row["nb_PRN"]),
             "Points jour":pts,"Score avant":prev_sc,
             "Score après":scores.get(m_sel),"Type":"Simple"}
        plans.append(rec); logs.append(rec.copy())
    planning_df=pd.DataFrame(plans).sort_values("Date").reset_index(drop=True)
    log_df=pd.DataFrame(logs)
    pointage_update_df=pointage_df.copy()
    return planning_df, log_df, pointage_update_df

# --- Guides et interface utilisateur ---
# Génération d'un PDF guide pour le gestionnaire
def make_guide_planner():
    packet = io.BytesIO()
    c = pdf_canvas.Canvas(packet)
    text = c.beginText(40, 800)
    for line in [
        "Guide du gestionnaire de planning",
        "1. Mettez à jour la liste DOCTORS en haut du script.",
        "2. Cliquez sur 'Générer modèle Excel' et envoyez-le aux médecins.",
        "3. Importez le fichier rempli, ajustez les paramètres sur la barre latérale.",
        "4. Téléchargez le planning, le log et le pointage.",
        "",
        "Principes :",
        "- Équité des scores (pointage moyen)",
        "- Priorité aux OUI",
        "- Exclusion des NON",
        "- Cap des week-ends par médecin"
    ]:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()
    packet.seek(0)
    return packet.getvalue()

# Génération d'un PDF guide pour le médecin
def make_guide_physician():
    packet = io.BytesIO()
    c = pdf_canvas.Canvas(packet)
    text = c.beginText(40, 800)
    for line in [
        "Guide du médecin pour saisie des disponibilités",
        "- OUI : vous préférez absolument cette date.",
        "- PRN : disponible au besoin.",
        "- NON : éviter cette date.",
        "",
        "Le planning respecte vos choix tout en garantissant l'équité."
    ]:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()
    packet.seek(0)
    return packet.getvalue()


def main():
    st.title("Planning de gardes optimisé")
    with st.sidebar.expander("📖 Guides & Consignes"):
    st.download_button(
        "Guide gestionnaire (.pdf)",
        make_guide_planner(),
        file_name="guide_gestionnaire.pdf",
        mime="application/pdf"
    )
    st.download_button(
        "Guide médecin (.pdf)",
        make_guide_physician(),
        file_name="guide_medecin.pdf",
        mime="application/pdf"
    )

