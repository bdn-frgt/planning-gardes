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
    # Gardes résidents (prérempli dates)
    gard_res_df = pd.DataFrame({"date": dates, "Points": ["" for _ in dates]})
    # Période précédente (vide)
    prev_df = pd.DataFrame(columns=["Date", "Médecin"])
    # Paramètres
    params_df = pd.DataFrame({
        "Paramètre": ["periods_ante","pts_sem_res","pts_sem_nores","pts_we_res","pts_we_nores"],
        "Valeur": [periods_ante, pts_sem_res, pts_sem_nores, pts_we_res, pts_we_nores]
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
        # validation OUI/PRN/NON pour chaque médecin
        first_row = 1
        last_row = total_days
        for col_idx in range(3, 3 + len(docs)):
            col_letter = chr(ord('A') + col_idx)
            ws.data_validation(
                f"{col_letter}{first_row+1}:{col_letter}{last_row+1}",
                {'validate': 'list', 'source': ['OUI','PRN','NON']}
            )
    output.seek(0)
    return output

# --- Implémentation de generate_planning ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None,
                      seuil_proximite=6, max_weekends=1, bonus_oui=5):
    # Placeholder simple : remplacez par votre vraie logique d’attribution
    planning_df = pd.DataFrame()
    log_df = pd.DataFrame()
    pointage_update_df = pointage_df.copy()
    return planning_df, log_df, pointage_update_df

# --- Guides PDF ---
def make_guide_planner():
    packet = io.BytesIO()
    c = pdf_canvas.Canvas(packet)
    text = c.beginText(40, 800)
    for line in [
        "Guide gestionnaire de planning",
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
        "", "Le planning garantit vos choix et l'équité."
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

    # Import et attribution
    st.markdown("## Import et attribution")
    uploaded = st.file_uploader("Importer fichier Excel (.xlsx)", type=["xlsx"])
    if uploaded:
        try:
            xls = pd.ExcelFile(uploaded)
            errs = validate_file(xls)
            if errs:
                st.error("Erreurs de format:
" + "
".join(errs))
                st.stop()

            dispo = xls.parse("Dispo Période")
            pointage = xls.parse("Pointage gardes")
            gardes = xls.parse("Gardes résidents")
            prev = xls.parse("Période précédente") if "Période précédente" in xls.sheet_names else None

            planning_df, log_df, pointage_update_df = generate_planning(
                dispo, pointage, gardes, prev, seuil, max_we, bonus_oui
            )

            st.subheader("🚑 Planning")
            st.dataframe(planning_df)
            buf1 = io.BytesIO(); planning_df.to_excel(buf1, index=False); buf1.seek(0)
            st.download_button("Télécharger planning", buf1, "planning_gardes.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            st.subheader("📋 Log détaillé")
            st.dataframe(log_df)
            buf2 = io.BytesIO(); log_df.to_excel(buf2, index=False); buf2.seek(0)
            st.download_button("Télécharger log", buf2, "planning_gardes_log.xlsx",
                               "application/vnd.openxmlformats-officedocument-spreadsheetml.sheet")

            st.subheader("📊 Pointage mis à jour")
            st.dataframe(pointage_update_df)
            buf3 = io.BytesIO(); pointage_update_df.to_excel(buf3, index=False); buf3.seek(0)
            st.download_button("Télécharger pointage", buf3, "pointage_gardes.xlsx",
                               "application/vnd.openxmlformats-officedocument-spreadsheetml.sheet")

        except Exception as e:
            import traceback
            st.error(f"❌ Erreur interne : {e}")
            st.text(traceback.format_exc())
            st.stop()

if __name__ == "__main__":
    main()
    main()
