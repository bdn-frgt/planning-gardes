import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from collections import defaultdict
import io
from reportlab.pdfgen import canvas as pdf_canvas

# --- Configuration ---
# Liste des médecins - copiez-collez vos noms EXACTEMENT entre guillemets, séparés par des virgules
DOCTORS = ["DrAlice", "DrBob", "DrCharlie"]

# Onglets et colonnes attendus
REQUIRED_SHEETS = ["Dispo Période", "Pointage gardes", "Gardes résidents"]
OPTIONAL_SHEETS = ["Période précédente"]
REQUIRED_COLUMNS = {
    "Dispo Période": ["Jour", "Moment", "Date"] + DOCTORS,
    "Pointage gardes": ["MD", "Score actualisé"],
    "Gardes résidents": ["date", "Points"],
}
PREV_COLUMNS = {"Période précédente": ["Date", "Médecin"]}

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
    if "Période précédente" in xls.sheet_names:
        df_prev = xls.parse("Période précédente")
        for col in PREV_COLUMNS["Période précédente"]:
            if col not in df_prev.columns:
                errors.append(f"Colonne manquante dans Période précédente: {col}")
    return errors

# --- Génération du template Excel avec validation de données ---
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
    pt_df = pd.DataFrame({"MD": docs, "Score actualisé": [0] * len(docs)})
    # Gardes résidents
    gard_res_df = pd.DataFrame({"date": dates, "Points": ["" for _ in dates]})
    # Période précédente
    prev_df = pd.DataFrame(columns=["Date", "Médecin"])
    # Paramètres
    params_df = pd.DataFrame({
        "Paramètre": ["periods_ante","pts_sem_res","pts_sem_nores","pts_we_res","pts_we_nores"],
        "Valeur": [periods_ante,pts_sem_res,pts_sem_nores,pts_we_res,pts_we_nores]
    })
    # Écriture Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        dispo_df.to_excel(writer, sheet_name="Dispo Période", index=False)
        pt_df.to_excel(writer, sheet_name="Pointage gardes", index=False)
        gard_res_df.to_excel(writer, sheet_name="Gardes résidents", index=False)
        prev_df.to_excel(writer, sheet_name="Période précédente", index=False)
        params_df.to_excel(writer, sheet_name="Paramètres", index=False)
        workbook = writer.book
        ws = writer.sheets["Dispo Période"]
        for col_idx in range(3, 3 + len(docs)):
            col_letter = chr(ord('A') + col_idx)
            ws.data_validation(
                f"{col_letter}2:{col_letter}{total_days+1}",
                {
                    'validate': 'list',
                    'source': ['OUI', 'PRN', 'NON']
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
    grd = gardes_df.copy()
    grd["date"] = pd.to_datetime(grd["date"])
    points_map = grd.set_index("date")["Points"].to_dict()
    df_g["Points jour"] = df_g["Date"].map(points_map).fillna(0).astype(int)
    for s in ["OUI","PRN","NON"]:
        df_g[f"nb_{s}"] = df_g[meds].apply(lambda r: sum(str(x).strip().upper()==s for x in r), axis=1)
    def weekend_id(d):
        wd = d.weekday()
        if wd == 4: return d
        if wd == 5: return d - timedelta(days=1)
        if wd == 6: return d - timedelta(days=2)
        return None
    df_g["we_id"] = df_g["Date"].apply(weekend_id)
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    history = defaultdict(list)
    we_count = defaultdict(int)
    if prev_df is not None:
        prev_df["Date"] = pd.to_datetime(prev_df["Date"])
        for _, r in prev_df.iterrows():
            history[r["Médecin"]].append(r["Date"])
            wid = weekend_id(r["Date"])
            if wid is not None:
                we_count[r["Médecin"]] += 1
    plans = []
    logs = []
    # Attribution week-ends
    for wid, grp in df_g[df_g["we_id"].notna()].groupby("we_id"):
        dates = sorted(grp["Date"])
        cands = []
        for m in meds:
            if we_count[m] >= max_weekends: continue
            stats = [str(grp.loc[grp["Date"]==d, m].iloc[0]).strip().upper() for d in dates]
            if stats.count("NON") == len(stats): continue
            base = scores.get(m, 0)
            bonus = stats.count("OUI") * bonus_oui
            cands.append((m, base - bonus, stats))
        if not cands: continue
        sel = min(cands, key=lambda x: x[1])[0]
        we_count[sel] += 1
        for d in dates:
            disp = str(df_g.loc[df_g["Date"]==d, sel].iloc[0]).strip().upper()
            prev_score = scores.get(sel, 0)
            pts = points_map.get(d, 0)
            scores[sel] = prev_score + pts
            history[sel].append(d)
            rec = {
                "Date": d,
                "Médecin": sel,
                "Statut": disp,
                "nb_OUI": int(df_g.loc[df_g["Date"]==d, "nb_OUI"].iloc[0]),
                "nb_PRN": int(df_g.loc[df_g["Date"]==d, "nb_PRN"].iloc[0]),
                "Points jour": pts,
                "Score avant": prev_score,
                "Score après": scores[sel],
                "Type": "WE"
            }
            plans.append(rec)
            logs.append(rec.copy())
    # Attribution jours simples
    simple = df_g[df_g["we_id"].isna()].sort_values(["nb_OUI", "nb_PRN", "Points jour"])
    for _, row in simple.iterrows():
        d = row["Date"]
        if any(p["Date"] == d for p in plans): continue
        cands = []
        for m in meds:
            disp = str(row[m]).strip().upper()
            if disp == "NON": continue
            if any(abs((d - x).days) < seuil_proximite for x in history[m]): continue
            base = scores.get(m, 0)
            bonus = bonus_oui if disp == "OUI" else 0
            cands.append((m, disp, base - bonus))
        if cands:
            sel_m, sel_disp, _ = min(cands, key=lambda x: x[2])
        else:
            sel_m, sel_disp = None, None
        prev_score = scores.get(sel_m, 0) if sel_m else None
        pts = row["Points jour"]
        if sel_m:
            scores[sel_m] = prev_score + pts
            history[sel_m].append(d)
        rec = {
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
        plans.append(rec)
        logs.append(rec.copy())
    planning_df = pd.DataFrame(plans).sort_values("Date").reset_index(drop=True)
    log_df = pd.DataFrame(logs)
    pointage_update_df = pointage_df.copy()
    return planning_df, log_df, pointage_update_df

# --- Guides PDF ---
def make_guide_planner():
    packet = io.BytesIO()
    c = pdf_canvas.Canvas(packet)
    text = c.beginText(40, 800)
    for line in [
        "Guide du gestionnaire de planning",
        "1. Mettez à jour DOCTORS en haut du script.",
        "2. Générez le modèle Excel et envoyez-le.",
        "3. Importez, ajustez les paramètres.",
        "4. Téléchargez planning, log et pointage.",
        "", "Principes : équité, priorité OUI, exclusion NON, cap WE"
    ]:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()
    packet.seek(0)
    return packet.getvalue()

def make_guide_physician():
    packet = io.BytesIO()
    c = pdf_canvas.Canvas(packet)
    text = c.beginText(40, 800)
    for line in [
        "Guide médecin pour saisie des disponibilités",
        "- OUI : préférence forte",
        "- PRN : disponible au besoin",
        "- NON : éviter cette date",
        "", "Le planning garantit vos choix tout en garantissant l'équité."
    ]:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()
    packet.seek(0)
    return packet.getvalue()

# --- Interface utilisateur ---
def main():
    st.set_page_config(page_title="Planning Gardes", layout="wide")
    st.title("Planning de gardes optimisé")

    # Guides téléchargeables
    with st.sidebar.expander("📖 Guides & Consignes", expanded=True):
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

    # Génération du modèle Excel d'entrée
    st.sidebar.header("Modèle Excel d'entrée")
    start_date = st.sidebar.date_input("Date de début", datetime.today().date())
    num_weeks = st.sidebar.number_input("Nombre de semaines", 1, 52, 4)
    periods_ante = st.sidebar.number_input("Périodes antérieures", 1, 12, 12)
    pts_sem_res = st.sidebar.number_input("Pts sem AVEC rés", 0, 10, 1)
    pts_sem_nores = st.sidebar.number_input("Pts sem SANS rés", 0, 10, 3)
    pts_we_res = st.sidebar.number_input("Pts WE AVEC rés", 0, 10, 3)
    pts_we_nores = st.sidebar.number_input("Pts WE SANS rés", 0, 10, 4)
    if st.sidebar.button("Générer modèle Excel"):
        tpl = create_template_excel(
            start_date, num_weeks, periods_ante,
            pts_sem_res, pts_sem_nores, pts_we_res, pts_we_nores
        )
        st.sidebar.download_button(
            "Télécharger modèle Excel", tpl,
            "template_planning_gardes.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    # Paramètres d'affectation
    st.sidebar.header("Paramètres d'affectation")
    seuil = st.sidebar.number_input("Seuil proximité (jours)", 1, 28, 6)
    max_we = st.sidebar.number_input("Max WE par médecin", 0, 52, 1)
    bonus_oui = st.sidebar.number_input("Bonus pour un OUI (pts)", 0, 100, 5)

    # Import initial et stockage en session
    uploaded = st.sidebar.file_uploader("Importer fichier Excel (.xlsx)", type=["xlsx"])
    if uploaded:
        xls = pd.ExcelFile(uploaded)
        errs = validate_file(xls)
        if errs:
            st.sidebar.error("Erreurs de format :
" + "
".join(errs))
            st.stop()
        st.session_state['dispo'] = xls.parse("Dispo Période")
        st.session_state['pointage'] = xls.parse("Pointage gardes")
        st.session_state['gardes'] = xls.parse("Gardes résidents")
        st.session_state['prev'] = (xls.parse("Période précédente")
                                    if "Période précédente" in xls.sheet_names else None)

    # Bouton de recalcul
    if 'dispo' in st.session_state:
        if st.sidebar.button("Recalculer le planning"):
            planning_df, log_df, pt_update_df = generate_planning(
                st.session_state['dispo'],
                st.session_state['pointage'],
                st.session_state['gardes'],
                st.session_state['prev'],
                seuil, max_we, bonus_oui
            )
            st.session_state['planning'] = planning_df
            st.session_state['log'] = log_df
            st.session_state['pt_update'] = pt_update_df

    # Affichage des résultats
    if 'planning' in st.session_state:
        st.subheader("🚑 Planning")
        st.dataframe(st.session_state['planning'])
        buf1 = io.BytesIO(); st.session_state['planning'].to_excel(buf1, index=False); buf1.seek(0)
        st.download_button("Télécharger planning", buf1, "planning_gardes.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.subheader("📋 Log détaillé")
        st.dataframe(st.session_state['log'])
        buf2 = io.BytesIO(); st.session_state['log'].to_excel(buf2, index=False); buf2.seek(0)
        st.download_button("Télécharger log", buf2, "planning_gardes_log.xlsx",
                           "application/vnd.openxmlformats-officedocument-spreadsheetml.sheet")

        st.subheader("📊 Pointage mis à jour")
        st.dataframe(st.session_state['pt_update'])
        buf3 = io.BytesIO(); st.session_state['pt_update'].to_excel(buf3, index=False); buf3.seek(0)
        st.download_button("Télécharger pointage", buf3, "pointage_gardes.xlsx",
                           "application/vnd.openxmlformats-officedocument-spreadsheetml.sheet")

if __name__ == "__main__":
    main()
