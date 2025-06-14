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
        "Paramètre": ["periods_ante", "pts_sem_res", "pts_sem_nores", "pts_we_res", "pts_we_nores"],
        "Valeur": [periods_ante, pts_sem_res, pts_sem_nores, pts_we_res, pts_we_nores]
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

# --- Génération du planning (stub) ---
def generate_planning(dispo_df, pointage_df, gardes_df, prev_df=None,
                      seuil_proximite=6, max_weekends=1, bonus_oui=5):
    # TODO: Insérer l'implémentation complète ici
    planning_df = pd.DataFrame()
    log_df = pd.DataFrame()
    pointage_update_df = pointage_df.copy()
    return planning_df, log_df, pointage_update_df

# --- Guides téléchargeables ---
def make_guide_planner():
    md = (
        "# Guide du gestionnaire\n"
        "1. Mettez à jour la liste DOCTORS en haut du script.\n"
        "2. Cliquez sur 'Générer le modèle Excel' et envoyez-le aux médecins.\n"
        "3. Importez le fichier rempli, ajustez les paramètres sur la barre latérale.\n"
        "4. Téléchargez le planning, le log et le pointage.\n"
        "\n"  
        "Principes : équité des scores, priorité aux OUI, exclusion des NON, cap WE."
    )
    return md.encode('utf-8')

def make_guide_physician():
    md = (
        "# Guide du médecin\n"
        "- OUI si vous préférez absolument cette date.\n"
        "- PRN si disponible au besoin.\n"
        "- NON si vous devez éviter cette date.\n"
        "\nLe planning respecte au mieux vos choix tout en garantissant l'équité."
    )
    return md.encode('utf-8')

# --- Interface utilisateur ---
def main():
    st.title("Planning de gardes optimisé")

    # Guides
    with st.sidebar.expander("📖 Guides & Consignes"):
        st.download_button("Guide gestionnaire (.md)", make_guide_planner(), "guide_gestionnaire.md", "text/markdown")
        st.download_button("Guide médecin (.md)", make_guide_physician(), "guide_medecin.md", "text/markdown")

    # Modèle Excel
    st.sidebar.header("Modèle Excel d'entrée")
    start_date = st.sidebar.date_input("Date de début", datetime.today().date())
    num_weeks = st.sidebar.number_input("Nombre de semaines", 1, 52, 4)
    periods_ante = st.sidebar.number_input("Périodes antérieures", 1, 12, 12)
    pts_sem_res = st.sidebar.number_input("Pt sem AVEC rés", 0, 10, 1)
    pts_sem_nores = st.sidebar.number_input("Pt sem SANS rés", 0, 10, 3)
    pts_we_res = st.sidebar.number_input("Pt WE AVEC rés", 0, 10, 3)
    pts_we_nores = st.sidebar.number_input("Pt WE SANS rés", 0, 10, 4)
    if st.sidebar.button("Générer modèle Excel"):
        tpl = create_template_excel(start_date, num_weeks, periods_ante,
                                    pts_sem_res, pts_sem_nores, pts_we_res, pts_we_nores)
        st.sidebar.download_button("Télécharger le modèle Excel", data=tpl,
                                   file_name="template_planning_gardes.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # Paramètres d'affectation
    st.sidebar.header("Paramètres d'affectation")
    seuil = st.sidebar.number_input("Seuil proximité (jours)", 1, 28, 6)
    max_we = st.sidebar.number_input("Max WE par médecin", 0, 52, 1)
    bonus_oui = st.sidebar.number_input("Bonus pour un OUI", 0, 100, 5)

    # Import des disponibilités
    uploaded = st.file_uploader("Importer fichier Excel (.xlsx)", type=["xlsx"])
    if uploaded:
        xls = pd.ExcelFile(uploaded)
        errs = validate_file(xls)
        if errs:
            st.error("Erreurs de format:\n" + "\n".join(errs))
            return
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
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.subheader("📊 Pointage")
        st.dataframe(pointage_update_df)
        buf3 = io.BytesIO(); pointage_update_df.to_excel(buf3, index=False); buf3.seek(0)
        st.download_button("Télécharger pointage", buf3, "pointage_gardes.xlsx",
                           "application/vnd.openxmlformats-officedocument-spreadsheetml.sheet")

if __name__ == "__main__":
    main()
