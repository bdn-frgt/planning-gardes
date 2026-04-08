import io
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from reportlab.pdfgen import canvas as pdf_canvas

# =========================
# Configuration par défaut
# =========================
# Cette liste sert seulement à générer le template initial.
# Ensuite, l'application lit automatiquement les médecins présents
# dans les colonnes de la feuille "Dispo Période".
DEFAULT_DOCTORS = ["Dr1", "Dr2", "Dr3", "Dr4", "Dr5"]

PREV_SHEET = "Période précédente"
BASE_DISPO_COLS = ["Jour", "Moment", "Date"]


# =========================
# Validation du fichier importé
# =========================
def validate_file(xls: pd.ExcelFile) -> list[str]:
    errors: list[str] = []

    # Dispo Période
    if "Dispo Période" not in xls.sheet_names:
        errors.append("Feuille manquante: Dispo Période")
    else:
        df = xls.parse("Dispo Période")
        for col in BASE_DISPO_COLS:
            if col not in df.columns:
                errors.append(f"Colonne manquante dans Dispo Période: {col}")
        meds_cols = [c for c in df.columns if c not in BASE_DISPO_COLS]
        if not meds_cols:
            errors.append("Aucune colonne médecin détectée dans Dispo Période")

    # Pointage gardes
    if "Pointage gardes" not in xls.sheet_names:
        errors.append("Feuille manquante: Pointage gardes")
    else:
        dfp = xls.parse("Pointage gardes")
        for col in ["MD", "Score actualisé"]:
            if col not in dfp.columns:
                errors.append(f"Colonne manquante dans Pointage gardes: {col}")

    # Gardes résidents
    if "Gardes résidents" not in xls.sheet_names:
        errors.append("Feuille manquante: Gardes résidents")
    else:
        dfr = xls.parse("Gardes résidents")
        for col in ["date", "résident", "Points"]:
            if col not in dfr.columns:
                errors.append(f"Colonne manquante dans Gardes résidents: {col}")

    # Période précédente (optionnelle)
    if PREV_SHEET in xls.sheet_names:
        dfprev = xls.parse(PREV_SHEET)
        for col in ["Date", "Médecin"]:
            if col not in dfprev.columns:
                errors.append(f"Colonne manquante dans {PREV_SHEET}: {col}")

    return errors


# =========================
# Template Excel
# =========================
def create_template_excel(
    start_date: date,
    num_weeks: int,
    periods_ante: int,
    pts_sem_res: int,
    pts_sem_nores: int,
    pts_we_res: int,
    pts_we_nores: int,
    doctors: list[str] | None = None,
) -> io.BytesIO:
    docs = doctors if doctors else DEFAULT_DOCTORS.copy()
    total_days = num_weeks * 7
    dates = [start_date + timedelta(days=i) for i in range(total_days)]

    dispo_rows = []
    for d in dates:
        dispo_rows.append(
            {
                "Jour": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"][d.weekday()],
                "Moment": "Soir" if d.weekday() < 5 else "",
                "Date": d,
            }
        )
    dispo_df = pd.DataFrame(dispo_rows)
    for m in docs:
        dispo_df[m] = "PRN"

    pt_df = pd.DataFrame({"MD": docs, "Score actualisé": [0] * len(docs)})

    gard_res_df = pd.DataFrame(
        {
            "date": dates,
            "résident": ["" for _ in dates],
            "Points": ["" for _ in dates],
        }
    )

    prev_df = pd.DataFrame(columns=["Date", "Médecin"])

    params_df = pd.DataFrame(
        {
            "Paramètre": [
                "periods_ante",
                "pts_sem_res",
                "pts_sem_nores",
                "pts_we_res",
                "pts_we_nores",
            ],
            "Valeur": [
                periods_ante,
                pts_sem_res,
                pts_sem_nores,
                pts_we_res,
                pts_we_nores,
            ],
        }
    )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        dispo_df.to_excel(writer, sheet_name="Dispo Période", index=False)
        pt_df.to_excel(writer, sheet_name="Pointage gardes", index=False)
        gard_res_df.to_excel(writer, sheet_name="Gardes résidents", index=False)
        prev_df.to_excel(writer, sheet_name=PREV_SHEET, index=False)
        params_df.to_excel(writer, sheet_name="Paramètres", index=False)

        ws_dispo = writer.sheets["Dispo Période"]
        for idx in range(3, 3 + len(docs)):
            col = chr(ord("A") + idx)
            ws_dispo.data_validation(
                f"{col}2:{col}{total_days + 1}",
                {"validate": "list", "source": ["OUI", "PRN", "NON"]},
            )

        # Feuille Gardes résidents : colonne B = résident, colonne C = Points
        ws_res = writer.sheets["Gardes résidents"]
        for i in range(total_days):
            r = i + 2
            formula = (
                f'=IF(B{r}<>"",'
                f'IF(WEEKDAY(A{r},2)<=5,{pts_sem_res},{pts_we_res}),'
                f'IF(WEEKDAY(A{r},2)<=5,{pts_sem_nores},{pts_we_nores}))'
            )
            ws_res.write_formula(f"C{r}", formula)

    output.seek(0)
    return output


# =========================
# Calcul du pointage mis à jour
# =========================
def update_pointage(
    pointage_df: pd.DataFrame,
    planning_df: pd.DataFrame,
    periods_ante: int = 12,
) -> pd.DataFrame:
    out = pointage_df.copy()
    if "MD" not in out.columns:
        return out

    current_points = (
        planning_df.groupby("Médecin")["Points jour"].sum().to_dict()
        if not planning_df.empty and "Médecin" in planning_df.columns
        else {}
    )

    out["Période_actuelle"] = out["MD"].map(current_points).fillna(0)

    excluded = {"MD", "Score actualisé", "Nouveau score", "Périodes_considerées"}
    historical_cols = [c for c in out.columns if c not in excluded and c != "Période_actuelle"]

    if periods_ante < 0:
        periods_ante = 0

    kept_historical_cols = historical_cols[-periods_ante:] if historical_cols else []
    cols_for_average = kept_historical_cols + ["Période_actuelle"]

    numeric_df = pd.DataFrame(index=out.index)
    for col in cols_for_average:
        numeric_df[col] = pd.to_numeric(out[col], errors="coerce")

    periods_present = numeric_df.notna().sum(axis=1)
    total_points = numeric_df.sum(axis=1, skipna=True)

    out["Périodes_considerées"] = periods_present
    out["Nouveau score"] = out.get("Score actualisé", 0)
    mask = periods_present > 0
    out.loc[mask, "Nouveau score"] = total_points[mask] / periods_present[mask]

    return out

    current_points = (
        planning_df.groupby("Médecin")["Points jour"].sum().to_dict()
        if not planning_df.empty and "Médecin" in planning_df.columns
        else {}
    )

    out["Période_actuelle"] = out["MD"].map(current_points).fillna(0)

    # Colonnes historiques numériques utilisables pour le recalcul
    excluded = {"MD", "Score actualisé", "Nouveau score"}
    candidate_cols = [c for c in out.columns if c not in excluded]

    numeric_df = pd.DataFrame(index=out.index)
    for col in candidate_cols:
        numeric_df[col] = pd.to_numeric(out[col], errors="coerce")

    n_periods = numeric_df.notna().sum(axis=1)
    total_points = numeric_df.sum(axis=1, skipna=True)

    out["Nouveau score"] = out["Score actualisé"]
    mask = n_periods > 0
    out.loc[mask, "Nouveau score"] = total_points[mask] / n_periods[mask]

    return out


# =========================
# Attribution des gardes
# =========================
def generate_planning(
    dispo_df: pd.DataFrame,
    pointage_df: pd.DataFrame,
    gardes_df: pd.DataFrame,
    prev_df: pd.DataFrame | None = None,
    seuil_proximite: int = 6,
    max_weekends: int = 1,
    bonus_oui: int = 5,
    periods_ante: int = 12,
):
    dispo = dispo_df.copy()
    dispo["Date"] = pd.to_datetime(dispo["Date"])

    meds = [c for c in dispo.columns if c not in BASE_DISPO_COLS]

    mask = (
        (dispo["Moment"].fillna("").astype(str).str.lower() == "soir")
        | (dispo["Date"].dt.weekday >= 5)
    )
    df = dispo[mask].copy()

    grd = gardes_df.copy()
    grd["date"] = pd.to_datetime(grd["date"])
    grd["Points"] = pd.to_numeric(grd["Points"], errors="coerce").fillna(0)
    pts_map = grd.set_index("date")["Points"].to_dict()

    df["Points jour"] = df["Date"].map(pts_map).fillna(0).astype(float)

    for s in ["OUI", "PRN", "NON"]:
        df[f"nb_{s}"] = df[meds].apply(
            lambda row: sum(str(x).strip().upper() == s for x in row), axis=1
        )

    def week_id(d):
        wd = d.weekday()
        if wd == 4:
            return d
        if wd == 5:
            return d - timedelta(days=1)
        if wd == 6:
            return d - timedelta(days=2)
        return None

    df["we_id"] = df["Date"].apply(week_id)

    pointage_local = pointage_df.copy()
    pointage_local["Score actualisé"] = pd.to_numeric(
        pointage_local["Score actualisé"], errors="coerce"
    ).fillna(0)
    scores = pointage_local.set_index("MD")["Score actualisé"].to_dict()
    history = defaultdict(list)
    we_count = defaultdict(int)

    if prev_df is not None and not prev_df.empty:
        prev_local = prev_df.copy()
        prev_local["Date"] = pd.to_datetime(prev_local["Date"])
        for _, r in prev_local.iterrows():
            md = r.get("Médecin")
            dt = r.get("Date")
            if pd.isna(md) or pd.isna(dt):
                continue
            history[md].append(dt)
            wid = week_id(dt)
            if wid is not None:
                we_count[md] += 1

    plans = []
    logs = []

    # Week-ends : attribution groupée vendredi-samedi-dimanche
    weekend_groups = []
    for wid, group in df[df["we_id"].notna()].groupby("we_id"):
        dates = sorted(group["Date"])
        hardest_row = group.sort_values(["nb_NON", "Date"], ascending=[False, True]).iloc[0]
        weekend_groups.append(
            {
                "wid": wid,
                "group": group,
                "dates": dates,
                "hardest_date": hardest_row["Date"],
                "hardest_non": int(hardest_row["nb_NON"]),
            }
        )

    # On attribue d'abord les week-ends les plus difficiles
    weekend_groups = sorted(
        weekend_groups,
        key=lambda x: (-x["hardest_non"], x["hardest_date"]),
    )

    for weekend_info in weekend_groups:
        wid = weekend_info["wid"]
        group = weekend_info["group"]
        dates = weekend_info["dates"]
        hardest_date = weekend_info["hardest_date"]
        hardest_non = weekend_info["hardest_non"]

        # 1) candidats respectant le cap de week-end
        eligible = [m for m in meds if we_count[m] < max_weekends]
        if not eligible:
            # fallback si le cap bloque tout
            eligible = meds.copy()

        candidate_rows = []
        for m in eligible:
            stats = [
                str(group.loc[group["Date"] == d, m].iloc[0]).strip().upper()
                for d in dates
            ]
            if stats.count("NON") == len(stats):
                continue

            n_oui = stats.count("OUI")
            n_prn = stats.count("PRN")
            n_non = stats.count("NON")
            base_score = scores.get(m, 0)

            # priorité explicite sur le bloc week-end
            if n_oui == 3:
                tier = 0
            elif n_oui == 2 and n_prn == 1:
                tier = 1
            elif n_oui == 2 and n_non == 1:
                tier = 2
            elif n_oui == 1 and n_prn == 2:
                tier = 3
            elif n_oui == 1 and n_prn == 1 and n_non == 1:
                tier = 4
            elif n_prn == 3:
                tier = 5
            elif n_prn == 2 and n_non == 1:
                tier = 6
            elif n_prn == 1 and n_non == 2:
                tier = 7
            else:
                tier = 8

            # score secondaire : favoriser les OUI mais garder l'équité
            adjusted_score = base_score - n_oui * bonus_oui

            candidate_rows.append(
                {
                    "md": m,
                    "tier": tier,
                    "adjusted_score": adjusted_score,
                    "base_score": base_score,
                    "stats": stats,
                    "n_oui": n_oui,
                    "n_prn": n_prn,
                    "n_non": n_non,
                }
            )

        if not candidate_rows:
            continue

        # tri principal = qualité globale du week-end, tri secondaire = équité/bonus OUI
        candidate_rows = sorted(
            candidate_rows,
            key=lambda x: (x["tier"], x["adjusted_score"], x["base_score"]),
        )
        best = candidate_rows[0]
        sel = best["md"]
        we_count[sel] += 1

        for d in dates:
            disp = str(df.loc[df["Date"] == d, sel].iloc[0]).strip().upper()
            prev_sc = scores.get(sel, 0)
            pts = pts_map.get(d, 0)
            scores[sel] = prev_sc + pts
            history[sel].append(d)
            rec = {
                "Date": d,
                "Médecin": sel,
                "Statut": disp,
                "Points jour": pts,
                "Score avant": prev_sc,
                "Score après": scores[sel],
                "Type": "WE",
                "Weekend_tier": best["tier"],
                "Weekend_oui": best["n_oui"],
                "Weekend_prn": best["n_prn"],
                "Weekend_non": best["n_non"],
                "Weekend_hardest_date": hardest_date,
                "Weekend_hardest_non": hardest_non,
            }
            plans.append(rec)
            logs.append(rec.copy())

    # Jours simples
    simple = df[df["we_id"].isna()].sort_values(["nb_OUI", "nb_PRN", "Points jour"])

    for _, row in simple.iterrows():
        d = row["Date"]
        if any(p["Date"] == d for p in plans):
            continue

        cands = []
        non_cands = []

        for m in meds:
            disp = str(row[m]).strip().upper()
            if any(abs((d - x).days) < seuil_proximite for x in history[m]):
                continue
            score = scores.get(m, 0) - (bonus_oui if disp == "OUI" else 0)
            if disp == "NON":
                non_cands.append((m, score, disp))
            else:
                cands.append((m, score, disp))

        if cands:
            sel, _, sel_disp = min(cands, key=lambda x: x[1])
        elif non_cands:
            sel, _, sel_disp = min(non_cands, key=lambda x: x[1])
        else:
            sel, sel_disp = None, None

        prev_sc = scores.get(sel, 0) if sel else None
        pts = row["Points jour"]
        if sel:
            scores[sel] = prev_sc + pts
            history[sel].append(d)

        rec = {
            "Date": d,
            "Médecin": sel,
            "Statut": sel_disp,
            "Points jour": pts,
            "Score avant": prev_sc,
            "Score après": scores.get(sel),
            "Type": "Simple",
        }
        plans.append(rec)
        logs.append(rec.copy())

    planning_df = pd.DataFrame(plans).sort_values("Date").reset_index(drop=True)
    log_df = pd.DataFrame(logs)
    pointage_update_df = update_pointage(pointage_df, planning_df, periods_ante=periods_ante)
    return planning_df, log_df, pointage_update_df


# =========================
# Guides PDF
# =========================
def make_guide_planner():
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf)
    t = c.beginText(40, 800)
    for l in [
        "Guide gestionnaire de planning",
        "1. Générez le modèle Excel.",
        "2. Chaque médecin remplit sa disponibilité (OUI/PRN/NON).",
        "3. Importez le fichier Excel.",
        "4. Ajustez les paramètres et cliquez sur Recalculer.",
        "5. Téléchargez planning, log et pointage.",
    ]:
        t.textLine(l)
    c.drawText(t)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def make_guide_physician():
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf)
    t = c.beginText(40, 800)
    for l in [
        "Guide médecin",
        "OUI = préférence forte",
        "PRN = disponible si besoin",
        "NON = à éviter",
        "Le système cherche un équilibre entre préférences et équité.",
    ]:
        t.textLine(l)
    c.drawText(t)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


# =========================
# Interface utilisateur
# =========================
def main():
    st.set_page_config(page_title="Planning Gardes", layout="wide")
    st.title("Planning de gardes optimisé")

    with st.sidebar.expander("📖 Guides & Consignes", expanded=True):
        st.download_button(
            "Guide gestionnaire (.pdf)",
            make_guide_planner(),
            "guide_gestionnaire.pdf",
            "application/pdf",
        )
        st.download_button(
            "Guide médecin (.pdf)",
            make_guide_physician(),
            "guide_medecin.pdf",
            "application/pdf",
        )

    st.sidebar.header("Modèle Excel d'entrée")
    sd = st.sidebar.date_input("Date de début", datetime.today().date())
    nw = st.sidebar.number_input("Nombre de semaines", 1, 52, 4)
    pa = st.sidebar.number_input("Périodes antérieures", 1, 12, 12)
    psr = st.sidebar.number_input("Pts sem AVEC rés", 0, 10, 1)
    psn = st.sidebar.number_input("Pts sem SANS rés", 0, 10, 3)
    pwr = st.sidebar.number_input("Pts WE AVEC rés", 0, 10, 3)
    pwn = st.sidebar.number_input("Pts WE SANS rés", 0, 10, 4)

    tpl = create_template_excel(sd, nw, pa, psr, psn, pwr, pwn)
    st.sidebar.download_button(
        "Télécharger modèle Excel",
        tpl,
        "template_planning_gardes.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.sidebar.header("Paramètres attribution")
    seuil = st.sidebar.number_input("Seuil proximité (jours)", 1, 28, 6)
    mw = st.sidebar.number_input("Max WE par médecin", 0, 52, 1)
    bo = st.sidebar.number_input("Bonus OUI (pts)", 0, 100, 5)

    up = st.sidebar.file_uploader("Importer fichier Excel (.xlsx)", type=["xlsx"])
    if up:
        xls = pd.ExcelFile(up)
        errs = validate_file(xls)
        if errs:
            st.sidebar.error("Erreurs de format :\n" + "\n".join(errs))
            st.stop()
        st.session_state["dispo"] = xls.parse("Dispo Période")
        st.session_state["pointage"] = xls.parse("Pointage gardes")
        st.session_state["gardes"] = xls.parse("Gardes résidents")
        st.session_state["prev"] = xls.parse(PREV_SHEET) if PREV_SHEET in xls.sheet_names else None
        if "Paramètres" in xls.sheet_names:
            params_df = xls.parse("Paramètres")
            try:
                params_map = dict(zip(params_df["Paramètre"], params_df["Valeur"]))
                st.session_state["periods_ante"] = int(params_map.get("periods_ante", 12))
            except Exception:
                st.session_state["periods_ante"] = 12
        else:
            st.session_state["periods_ante"] = 12

    if "dispo" in st.session_state:
        if st.sidebar.button("Recalculer le planning"):
            p, l, pt = generate_planning(
                st.session_state["dispo"],
                st.session_state["pointage"],
                st.session_state["gardes"],
                st.session_state["prev"],
                seuil,
                mw,
                bo,
                st.session_state.get("periods_ante", 12),
            )
            st.session_state["planning"] = p
            st.session_state["log"] = l
            st.session_state["pt_update"] = pt

    if "planning" in st.session_state:
        st.subheader("🚑 Planning")
        st.dataframe(st.session_state["planning"])
        buf1 = io.BytesIO()
        st.session_state["planning"].to_excel(buf1, index=False)
        buf1.seek(0)
        st.download_button(
            "Télécharger planning",
            buf1,
            "planning.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.subheader("📋 Log détaillé")
        st.dataframe(st.session_state["log"])
        buf2 = io.BytesIO()
        st.session_state["log"].to_excel(buf2, index=False)
        buf2.seek(0)
        st.download_button(
            "Télécharger log",
            buf2,
            "log.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.subheader("📊 Pointage mis à jour")
        st.dataframe(st.session_state["pt_update"])
        buf3 = io.BytesIO()
        st.session_state["pt_update"].to_excel(buf3, index=False)
        buf3.seek(0)
        st.download_button(
            "Télécharger pointage",
            buf3,
            "pointage.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
