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

    # Identifie les groupes de week-end (id = date du vendredi)
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

    # Jour le plus difficile par week-end (max nb_NON)
    hardest = {}
    for wid, grp in df_gardes.groupby("weekend_id"):
        if pd.isna(wid): continue
        target = grp.sort_values(["nb_NON", "Date"], ascending=[False, True]).iloc[0]["Date"]
        hardest[wid] = target

    # Construction de l'ordre: jours de semaine + hardest du week-end
    df_order = df_gardes[df_gardes["weekend_id"].isna()]
    df_wd = df_gardes[~df_gardes["weekend_id"].isna()]
    df_order = pd.concat([df_order,
        df_wd[df_wd.apply(lambda r: r["Date"] == hardest[r["weekend_id"]], axis=1)]
    ])
    df_order = df_order.sort_values(["nb_OUI", "nb_PRN", "Points"]).reset_index(drop=True)

    # Initialisation des scores et historique
    scores = pointage_df.set_index("MD")["Score actualisé"].to_dict()
    historique = defaultdict(list)
    utilisés = set()
    processed = set()
    logs = []
    assigns = []

    def est_proche(date, hist):
        return any(abs((date - d).days) < seuil_proximite for d in hist)

    # Parcours pour attribution
    for _, row in df_order.iterrows():
        date = row["Date"]
        wid = row["weekend_id"]
        # Skip si non hardest ou déjà traité
        if pd.notna(wid):
            if date != hardest[wid] or wid in processed:
                continue

        # Préparation candidats pour ce jour
        meds_row = [(m, str(row[m]).upper(), scores.get(m,0), historique[m].copy())
                    for m in meds if str(row[m]).upper()!="NON"]
        log = {"Date":date, "Jour":row["Jour"].lower(),
               "Points jour":row["Points"],
               "nb_OUI":row["nb_OUI"], "nb_PRN":row["nb_PRN"],
               "Liste candidats": ", ".join([f"{m}({d},{scores[m]})" for m,d,_,_ in meds_row])}

        # Sélection pour week-end (3 jours)
        sel_nom = None; raison = ""
        if pd.notna(wid):
            grp = df_gardes[df_gardes["weekend_id"]==wid]
            # Statuts par doc
            statuts = {m: list(grp[m].str.strip().upper()) for m in meds}
            # 1) ceux avec 3 OUI
            candidats3 = [(m,scores[m]) for m,sts in statuts.items() if sts.count("OUI")==3]
            if candidats3:
                sel_nom = min(candidats3, key=lambda x: x[1])[0]
                raison = "3 OUI week-end"
            else:
                # 2) 2 OUI + 1 PRN
                candidats2 = [ (m,scores[m]) for m,sts in statuts.items() if sts.count("OUI")==2 and sts.count("PRN")==1]
                if candidats2:
                    sel_nom = min(candidats2, key=lambda x: x[1])[0]
                    raison = "2 OUI + 1 PRN week-end"
            # 3) fallback aux pools classiques si pas de sel_nom

        # Sélection standard
        if not sel_nom:
            # Pools OUI puis PRN
            pool_oui = [c for c in meds_row if c[1]=="OUI"]
            pool_prn = [c for c in meds_row if c[1]=="PRN"]
            sel = None
            for pool_name,pool in [("OUI",pool_oui),("PRN",pool_prn)]:
                for cand in sorted(pool,key=lambda x:x[2]):
                    if not est_proche(date,cand[3]):
                        sel=cand; raison=f"Pool {pool_name}, score bas"
                        break
                if sel: break
            if not sel:
                sel = min(meds_row,key=lambda x:x[2]); raison="Score le plus faible"
            sel_nom, _, sel_score_before, _ = sel
        sel_score_after = scores.get(sel_nom,0)+row["Points"]
        scores[sel_nom] = sel_score_after
        historique[sel_nom].append(date)

        # Log attribution
        log.update({"Sélection":sel_nom, "Raison":raison,
                    "Score avant":sel_score_before if 'sel_score_before' in locals() else None,
                    "Score après":sel_score_after})
        logs.append(log); assigns.append({**log, "Médecin":sel_nom})
        utilisés.add(date)

        # Si week-end, assigner les 3 jours
        if pd.notna(wid):
            for other in statuts.keys(): pass  # placeholder
            for d in df_gardes[df_gardes["weekend_id"]==wid]["Date"]:
                if d==date: continue
                pts2 = int(df_gardes.loc[df_gardes["Date"]==d,"Points"].iloc[0])
                scores[sel_nom]+=pts2; historique[sel_nom].append(d)
                log2 = {"Date":d, "Jour":df_gardes.loc[df_gardes["Date"]==d,"Jour"].iloc[0].lower(),
                        "Médecin":sel_nom, "Raison":"Groupement week-end"}
                logs.append(log2); assigns.append(log2)
            processed.add(wid)

    return pd.DataFrame(assigns), pd.DataFrame(logs)

# --- Interface utilisateur ---

def main():
    st.title("Planning de gardes - 28 jours avec log détaillé")
    st.markdown("Chargez un fichier Excel structuré avec les feuilles **Dispo Période**, **Pointage gardes**, et **Gardes résidents**.")
    uploaded=st.file_uploader("Sélectionnez votre fichier Excel (.xlsx)",type=["xlsx"])
    seuil=st.number_input("Seuil de proximité (jours)",min_value=1,max_value=28,value=6)
    if uploaded:
        xls=pd.ExcelFile(uploaded); errs=validate_file(xls)
        if errs: st.error("Erreurs de format:\n"+"\n".join(errs)); return
        dispo,pointage,gardes = xls.parse("Dispo Période"),xls.parse("Pointage gardes"),xls.parse("Gardes résidents")
        planning,logs=generate_planning(dispo,pointage,gardes,seuil)
        st.subheader("Planning de gardes"); st.dataframe(planning)
        b1=io.BytesIO(); planning.to_excel(b1,index=False,sheet_name="Planning"); b1.seek(0)
        st.download_button("Télécharger planning (Excel)",b1,"planning_gardes.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.subheader("Log détaillé d'attribution"); st.dataframe(logs)
        b2=io.BytesIO(); logs.to_excel(b2,index=False,sheet_name="Log"); b2.seek(0)
        st.download_button("Télécharger log (Excel)",b2,"planning_gardes_log.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__=="__main__": main()
