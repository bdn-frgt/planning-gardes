import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from collections import defaultdict
import io
from reportlab.pdfgen import canvas as pdf_canvas

# --- Configuration ---
# Liste des médecins - copiez-collez vos noms entre guillemets, séparés par des virgules
DOCTORS = ["DrAlice", "DrBob", "DrCharlie"]

# Feuilles et colonnes attendues
REQUIRED_SHEETS = ["Dispo Période", "Pointage gardes", "Gardes résidents"]
PREV_SHEET = "Période précédente"
REQUIRED_COLUMNS = {
    "Dispo Période": ["Jour", "Moment", "Date"] + DOCTORS,
    "Pointage gardes": ["MD", "Score actualisé"],
    "Gardes résidents": ["date", "résident", "Points"],
}
PREV_COLUMNS = {PREV_SHEET: ["Date", "Médecin"]}

# --- Validation du fichier importé ---
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
    if PREV_SHEET in xls.sheet_names:
        df_prev = xls.parse(PREV_SHEET)
        for col in PREV_COLUMNS[PREV_SHEET]:
            if col not in df_prev.columns:
                errors.append(f"Colonne manquante dans {PREV_SHEET}: {col}")
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
        dispo_rows.append({
            "Jour": d.strftime("%A"),
            "Moment": "Soir" if d.weekday() < 5 else "",
            "Date": d
        })
    dispo_df = pd.DataFrame(dispo_rows)
    for m in docs:
        dispo_df[m] = "PRN"

    # Pointage gardes
    pt_df = pd.DataFrame({"MD": docs, "Score actualisé": [0]*len(docs)})

    # Gardes résidents avec formules
    gard_res_df = pd.DataFrame({
        "date": dates,
        "résident": ["" for _ in dates],
        "Points": ["" for _ in dates]
    })

    # Période précédente
    prev_df = pd.DataFrame(columns=["Date", "Médecin"])

    # Paramètres
    params_df = pd.DataFrame({
        "Paramètre": ["periods_ante","pts_sem_res","pts_sem_nores","pts_we_res","pts_we_nores"],
        "Valeur": [periods_ante, pts_sem_res, pts_sem_nores, pts_we_res, pts_we_nores]
    })

    # Écriture Excel en mémoire
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        dispo_df.to_excel(writer, sheet_name="Dispo Période", index=False)
        pt_df.to_excel(writer, sheet_name="Pointage gardes", index=False)
        gard_res_df.to_excel(writer, sheet_name="Gardes résidents", index=False)
        prev_df.to_excel(writer, sheet_name=PREV_SHEET, index=False)
        params_df.to_excel(writer, sheet_name="Paramètres", index=False)

        wb = writer.book
        # Validation OUI/PRN/NON
        ws = writer.sheets["Dispo Période"]
        for idx in range(3, 3+len(docs)):
            col = chr(ord('A')+idx)
            ws.data_validation(
                f"{col}2:{col}{total_days+1}",
                {'validate': 'list', 'source': ['OUI','PRN','NON']}
            )
        # Formules résidents
        ws2 = writer.sheets["Gardes résidents"]
        for i in range(total_days):
            r = i+2
            formula = (
                f"=IF(B{r}<>\"\","  \
                f"IF(WEEKDAY(A{r},2)<=5,{pts_sem_res},{pts_we_res}),"  \
                f"IF(WEEKDAY(A{r},2)<=5,{pts_sem_nores},{pts_we_nores}))"
            )
            ws2.write_formula(f"C{r}", formula)
    output.seek(0)
    return output

# --- Attribution des gardes ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None,
                      seuil_proximite=6, max_weekends=1, bonus_oui=5):
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])
    meds = DOCTORS
    mask = (dispo["Moment"].str.lower()=="soir") | dispo["Jour"].str.lower().isin(["samedi","dimanche"])
    df = dispo[mask].copy()
    grd = gardes_df.copy(); grd["date"] = pd.to_datetime(grd["date"])
    pts_map = grd.set_index("date")["Points"].to_dict()
    df["Points jour"] = df["Date"].map(pts_map).fillna(0).astype(int)
    for s in ["OUI","PRN","NON"]:
        df[f"nb_{s}"] = df[meds].apply(lambda row: sum(str(x).strip().upper()==s for x in row), axis=1)
    def week_id(d):
        wd = d.weekday()
        if wd==4: return d
        if wd==5: return d - timedelta(days=1)
        if wd==6: return d - timedelta(days=2)
        return None
    df["we_id"] = df["Date"].apply(week_id)
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    history = defaultdict(list)
    we_count = defaultdict(int)
    if prev_df is not None:
        prev_df["Date"] = pd.to_datetime(prev_df["Date"])
        for _, r in prev_df.iterrows():
            history[r["Médecin"]].append(r["Date"])
            wid = week_id(r["Date"])
            if wid: we_count[r["Médecin"]]+=1
    plans, logs = [], []
    # Week-ends
    for wid, group in df[df["we_id"].notna()].groupby("we_id"):
        dates = sorted(group["Date"])
        # candidats normaux
        cands = []
        for m in meds:
            if we_count[m] < max_weekends:
                stats = [str(group.loc[group["Date"]==d, m].iloc[0]).strip().upper() for d in dates]
                if stats.count("NON") < len(stats):
                    cands.append((m, scores.get(m,0) - stats.count("OUI")*bonus_oui))
        # fallback si vide
        if not cands:
            for m in meds:
                stats = [str(group.loc[group["Date"]==d, m].iloc[0]).strip().upper() for d in dates]
                if stats.count("NON") < len(stats):
                    cands.append((m, scores.get(m,0)))
        if not cands: continue
        sel = min(cands, key=lambda x: x[1])[0]
        we_count[sel] += 1
        for d in dates:
            disp = str(df.loc[df["Date"]==d, sel].iloc[0]).strip().upper()
            prev_sc = scores.get(sel,0)
            pts = pts_map.get(d,0)
            scores[sel] = prev_sc + pts
            history[sel].append(d)
            rec = {"Date": d, "Médecin": sel, "Statut": disp,
                   "Points jour": pts, "Score avant": prev_sc,
                   "Score après": scores[sel], "Type": "WE"}
            plans.append(rec); logs.append(rec.copy())
    # Jours simples
    simple = df[df["we_id"].isna()].sort_values(["nb_OUI","nb_PRN","Points jour"])
    for _, row in simple.iterrows():
        d = row["Date"]
        if any(p["Date"]==d for p in plans): continue
        cands = []
        for m in meds:
            disp = str(row[m]).strip().upper()
            if disp == "NON": continue
            if any(abs((d-x).days) < seuil_proximite for x in history[m]): continue
            cands.append((m, scores.get(m,0) - (bonus_oui if disp=="OUI" else 0)))
        sel = min(cands, key=lambda x: x[1])[0] if cands else None
        prev_sc = scores.get(sel,0) if sel else None
        pts = row["Points jour"]
        if sel:
            scores[sel] = prev_sc + pts
            history[sel].append(d)
        rec = {"Date": d, "Médecin": sel, "Statut": disp,
               "Points jour": pts, "Score avant": prev_sc,
               "Score après": scores.get(sel), "Type": "Simple"}
        plans.append(rec); logs.append(rec.copy())
    planning_df = pd.DataFrame(plans).sort_values("Date").reset_index(drop=True)
    log_df = pd.DataFrame(logs)
    # pointage non modifié ici
    return planning_df, log_df, pointage_df.copy()

# --- Guides PDF ---
def make_guide_planner():
    buf = io.BytesIO(); c = pdf_canvas.Canvas(buf)
    t = c.beginText(40,800)
    for l in ["Guide gestionnaire de planning","1. Mettez à jour DOCTORS","2. Génez modèle Excel","3. Importez et recalcul","4. Téléchargez résultats"]:
        t.textLine(l)
    c.drawText(t); c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()

def make_guide_physician():
    buf = io.BytesIO(); c = pdf_canvas.Canvas(buf)
    t = c.beginText(40,800)
    for l in ["Guide médecin: OUI/PRN/NON","Planning équitable"]:
        t.textLine(l)
    c.drawText(t); c.showPage(); c.save(); buf.seek(0)
    return buf.getvalue()

# --- Interface utilisateur ---
def main():
    st.set_page_config(page_title="Planning Gardes", layout="wide")
    st.title("Planning de gardes optimisé")
    with st.sidebar.expander("📖 Guides & Consignes", expanded=True):
        st.download_button("Guide gestionnaire (.pdf)", make_guide_planner(), "guide_gest.pdf", "application/pdf")
        st.download_button("Guide médecin (.pdf)", make_guide_physician(), "guide_med.pdf", "application/pdf")
    st.sidebar.header("Modèle Excel d'entrée")
    sd = st.sidebar.date_input("Date de début", datetime.today().date())
    nw = st.sidebar.number_input("Nombre de semaines", 1, 52, 4)
    pa = st.sidebar.number_input("Périodes antérieures", 1, 12, 12)
    psr=st.sidebar.number_input("Pts sem AVEC rés",0,10,1)
    psn=st.sidebar.number_input("Pts sem SANS rés",0,10,3)
    pwr=st.sidebar.number_input("Pts WE AVEC rés",0,10,3)
    pwn=st.sidebar.number_input("Pts WE SANS rés",0,10,4)
    if st.sidebar.button("Générer modèle Excel"):
        tpl = create_template_excel(sd,nw,pa,psr,psn,pwr,pwn)
        st.sidebar.download_button("Télécharger modèle Excel", tpl, "template.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.sidebar.header("Paramètres attribution")
    seuil = st.sidebar.number_input("Seuil proximité (jours)",1,28,6)
    mw = st.sidebar.number_input("Max WE par médecin",0,52,1)
    bo = st.sidebar.number_input("Bonus OUI (pts)",0,100,5)
    up = st.sidebar.file_uploader("Importer fichier Excel (.xlsx)", type=["xlsx"])
    if up:
        xls = pd.ExcelFile(up); errs = validate_file(xls)
        if errs: st.sidebar.error("Erreurs de format: \n"+"\n".join(errs)); st.stop()
        st.session_state['dispo'] = xls.parse("Dispo Période")
        st.session_state['pointage'] = xls.parse("Pointage gardes")
        st.session_state['gardes'] = xls.parse("Gardes résidents")
        st.session_state['prev'] = xls.parse(PREV_SHEET) if PREV_SHEET in xls.sheet_names else None
    if 'dispo' in st.session_state:
        if st.sidebar.button("Recalculer le planning"):
            p, l, pt = generate_planning(
                st.session_state['dispo'], st.session_state['pointage'],
                st.session_state['gardes'], st.session_state['prev'],
                seuil, mw, bo
            )
            st.session_state['planning'], st.session_state['log'], st.session_state['pt_update'] = p, l, pt
    if 'planning' in st.session_state:
        st.subheader("🚑 Planning"); st.dataframe(st.session_state['planning'])
        buf1 = io.BytesIO(); st.session_state['planning'].to_excel(buf1,index=False); buf1.seek(0)
        st.download_button("Télécharger planning", buf1, "planning.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.subheader("📋 Log détaillé"); st.dataframe(st.session_state['log'])
        buf2 = io.BytesIO(); st.session_state['log'].to_excel(buf2,index=False); buf2.seek(0)
        st.download_button("Télécharger log", buf2, "log.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.subheader("📊 Pointage mis à jour"); st.dataframe(st.session_state['pt_update'])
        buf3 = io.BytesIO(); st.session_state['pt_update'].to_excel(buf3,index=False); buf3.seek(0)
        st.download_button("Télécharger pointage", buf3, "pointage.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
if __name__ == "__main__": main()
