import streamlit as st
import pandas as pd
from datetime import timedelta
from collections import defaultdict
import io

# --- Utils ---

REQUIRED_SHEETS = ["Dispo Période", "Pointage gardes", "Gardes résidents"]
REQUIRED_COLUMNS = {
    "Dispo Période": ["Jour", "Moment", "Date"],
    "Pointage gardes": ["MD", "Score actualisé"],
    "Gardes résidents": ["dates", "date", "Points"]
}

# Vérification format

def validate_file(xls):
    missing = []
    for sheet in REQUIRED_SHEETS:
        if sheet not in xls.sheet_names:
            missing.append(f"Feuille manquante: {sheet}")
        else:
            df = xls.parse(sheet)
            for col in REQUIRED_COLUMNS[sheet]:
                if col not in df.columns:
                    missing.append(f"Colonne manquante dans {sheet}: {col}")
    return missing

# Génération du planning

def generate_planning(dispo_df, pointage_df, gardes_df, seuil_proximite=6):
    # Préparation
    dispo_df["Date"] = pd.to_datetime(dispo_df["Date"])
    mask = (dispo_df["Moment"].str.lower()=="soir") | (dispo_df["Jour"].str.lower().isin(["samedi","dimanche"]))
    df_gardes = dispo_df[mask].copy()
    # Médecins
    meds = [c for c in df_gardes.columns if c.startswith("Dr")]
    # Comptage dispo
    for status in ["OUI","PRN","NON"]:
        df_gardes[f"nb_{status}"] = df_gardes[meds].apply(lambda r: sum(str(x).strip().upper()==status for x in r), axis=1)
    # Points
    gardes_df["date"] = pd.to_datetime(gardes_df["date"])
    pts_map = gardes_df.set_index("date")["Points"].to_dict()
    df_gardes["Points"] = df_gardes["Date"].map(pts_map).fillna(0).astype(int)
    # Tri
    df_gardes = df_gardes.sort_values(["nb_OUI","nb_PRN","Points"])
    # Initialisation scores
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict().copy()
    hist = defaultdict(list)
    used = set()
    assigns = []
    # Proximité
    def proche(d, hist_list):
        return any(abs((d - h).days) < seuil_proximite for h in hist_list)
    # Attribution iterative
    for _, row in df_gardes.iterrows():
        d, j, pts = row["Date"], row["Jour"], row["Points"]
        if d in used:
            continue
        # candidats dispo
        cands = [(m, str(row[m]).upper(), scores.get(m,0)) for m in meds if str(row[m]).upper()!="NON"]
        if not cands:
            assigns.append({"Date":d, "Jour":j, "Médecin":"À assigner", "Points":pts})
            used.add(d)
            continue
        oui = [c for c in cands if c[1]=="OUI"]
        prn = [c for c in cands if c[1]=="PRN"]
        sel = None
        for pool in (oui, prn):
            for c in sorted(pool, key=lambda x: x[2]):
                if not proche(d, hist[c[0]]):
                    sel = c; break
            if sel: break
        if not sel:
            sel = min(cands, key=lambda x: x[2])
        doc = sel[0]
        scores[doc] += pts
        hist[doc].append(d)
        assigns.append({"Date":d, "Jour":j, "Médecin":doc, "Points":pts})
        used.add(d)
    return pd.DataFrame(assigns).sort_values("Date")

# --- UI ---

def main():
    st.title("Planning de gardes - 28 jours")
    st.markdown("Chargez un fichier Excel structuré avec les feuilles **Dispo Période**, **Pointage gardes**, et **Gardes résidents**.")
    uploaded = st.file_uploader("Sélectionnez votre fichier Excel (.xlsx)", type=["xlsx"] )
    seuil = st.number_input("Seuil de proximité (jours)", min_value=1, max_value=28, value=6)
    if uploaded:
        try:
            xls = pd.ExcelFile(uploaded)
            errors = validate_file(xls)
            if errors:
                st.error("Erreurs de format :\n" + "\n".join(errors))
                return
            dispo = xls.parse("Dispo Période")
            pointage = xls.parse("Pointage gardes")
            gardes = xls.parse("Gardes résidents")
            planning = generate_planning(dispo, pointage, gardes, seuil)
            st.success("Planning généré ! Nombre de jours : " + str(planning["Date"].nunique()))
            st.dataframe(planning)
            # Téléchargement
            towrite = io.BytesIO()
            planning.to_excel(towrite, index=False, sheet_name="Planning")
            towrite.seek(0)
            st.download_button("Télécharger le planning en Excel", data=towrite, file_name="planning_gardes_28jours.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.error(f"Erreur lors du traitement : {e}")

if __name__ == "__main__":
    main()
