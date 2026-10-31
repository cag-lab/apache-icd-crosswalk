"""APACHE II score and predicted hospital mortality, given an Acute Physiology Score.

    APACHE II = APS + age points + chronic health points

    logit = -3.517 + 0.146 * APACHE II + 0.603 (if after emergency surgery) + diagnostic category weight
    predicted mortality = 1 / (1 + exp(-logit))

Knaus WA, Draper EA, Wagner DP, Zimmerman JE. APACHE II: a severity of disease
classification system. Crit Care Med. 1985;13(10):818-829.

Chronic health status and the diagnostic category are read from ICD-10 codes
using the tables in data/; both can be overridden per stay.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .match import (
    CM, DATA_DIR, PCS, _bool, _prefixes, flag_stays, is_surgical, load_categories,
    load_code_map, load_surgery_exclusions, normalize, principal_categories,
)

INTERCEPT = -3.517
SCORE_COEF = 0.146
EMERGENCY_SURGERY_COEF = 0.603

NONOPERATIVE, ELECTIVE, EMERGENCY = "nonoperative", "elective", "emergency"
_STATUS_ALIASES = {
    NONOPERATIVE: NONOPERATIVE, "non-operative": NONOPERATIVE, "nonop": NONOPERATIVE, "medical": NONOPERATIVE,
    ELECTIVE: ELECTIVE, "elective postop": ELECTIVE, "elective postoperative": ELECTIVE, "elective surgery": ELECTIVE,
    EMERGENCY: EMERGENCY, "emergency postop": EMERGENCY, "emergency postoperative": EMERGENCY,
    "emergency surgery": EMERGENCY, "emergent": EMERGENCY, "urgent": EMERGENCY,
}


# --------------------------------------------------------------------------- #
# Scalar pieces
# --------------------------------------------------------------------------- #
def operative_status(value) -> str:
    """Normalize to 'nonoperative', 'elective' or 'emergency'."""
    key = str(value).strip().lower()
    if key not in _STATUS_ALIASES:
        raise ValueError(f"operative status {value!r}: use 'nonoperative', 'elective' or 'emergency'")
    return _STATUS_ALIASES[key]


def age_points(age: float) -> int:
    """<=44: 0, 45-54: 2, 55-64: 3, 65-74: 5, >=75: 6."""
    if age <= 44:
        return 0
    if age <= 54:
        return 2
    if age <= 64:
        return 3
    if age <= 74:
        return 5
    return 6


def chronic_health_points(has_chronic_condition: bool, status: str) -> int:
    """5 for non-operative or emergency post-op patients, 2 for elective post-op, 0 if no condition."""
    if not has_chronic_condition:
        return 0
    return 2 if operative_status(status) == ELECTIVE else 5


def apache2_total(aps: float, age: float, has_chronic_condition: bool, status: str) -> float:
    return aps + age_points(age) + chronic_health_points(has_chronic_condition, status)


def predicted_mortality(score: float, dx_weight: float, status: str) -> float:
    logit = INTERCEPT + SCORE_COEF * score + dx_weight
    if operative_status(status) == EMERGENCY:
        logit += EMERGENCY_SURGERY_COEF
    return 1.0 / (1.0 + math.exp(-logit))


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def load_weights(path: str | Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "knaus_weights.csv", dtype=str, keep_default_na=False)
    df["weight"] = df["weight"].astype(float)
    df["use_nonoperative_weight_if_postop"] = _bool(df["use_nonoperative_weight_if_postop"])
    df["order"] = range(len(df))
    return df


def _load(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / name, dtype=str, keep_default_na=False)


# --------------------------------------------------------------------------- #
# Diagnostic category
# --------------------------------------------------------------------------- #
class _KnausPicker:
    def __init__(self, weights: pd.DataFrame):
        self.w = weights.set_index(["setting", "knaus_category"])
        self.dx_map = _load("knaus_dx_map.csv")
        self.procs = _load("knaus_postop_procedures.csv")
        fb = _load("knaus_chapter_fallback.csv")
        self.fallback = sorted(zip(fb["icd_prefix"].map(normalize), fb["system"]), key=lambda x: -len(x[0]))
        self.nonop_always = set(weights.loc[weights["use_nonoperative_weight_if_postop"], "knaus_category"])
        self.excl = load_surgery_exclusions()
        self.by_cat: dict[str, list[dict]] = {}
        for r in self.dx_map.to_dict("records"):
            self.by_cat.setdefault(r["category"], []).append(r)

    def weight(self, setting, category):
        return float(self.w.at[(setting, category), "weight"])

    def _rank(self, setting, category):
        row = self.w.loc[(setting, category)]
        return (0 if row["kind"] == "specific" else 1, int(row["order"]))

    def map_row(self, category, principal_n):
        rows = self.by_cat.get(category, [])
        for r in rows:  # code-level overrides first
            if r["code_prefixes"] and principal_n.startswith(_prefixes(r["code_prefixes"])):
                return r
        for r in rows:
            if not r["code_prefixes"]:
                return r
        return None

    def chapter(self, principal_n):
        for prefix, system in self.fallback:
            if principal_n.startswith(prefix):
                return system
        return None

    def pick(self, status, cats, principal_n, dx_all, pcs_all):
        """Return (setting, knaus_category, source) or (None, None, reason)."""
        rows = [(c, self.map_row(c, principal_n)) for c in cats]
        rows = [(c, r) for c, r in rows if r]
        nonop = [(c, r["nonoperative_knaus"]) for c, r in rows if r["nonoperative_knaus"]]

        if status == NONOPERATIVE:
            if nonop:
                c, k = min(nonop, key=lambda x: self._rank("nonoperative", x[1]))
                return "nonoperative", k, f"principal diagnosis: {c}"
            system = self.chapter(principal_n)
            if system:
                return "nonoperative", system, f"ICD chapter of principal diagnosis ({principal_n[:3]})"
            return None, None, "no Knaus category for the principal diagnosis"

        # post-operative
        for c, k in nonop:  # sepsis / post-arrest -> non-operative weight (Knaus)
            if k in self.nonop_always:
                return "nonoperative", k, f"principal diagnosis: {c} (post-op sepsis/arrest use non-operative weight)"
        postop = [(c, r["postoperative_knaus"]) for c, r in rows if r["postoperative_knaus"]]
        surg = [p for p in pcs_all if is_surgical(p, self.excl)]
        procs = self.procs.to_dict("records")

        def proc_match(rules):
            for r in rules:
                pfx, req = _prefixes(r["pcs_prefixes"]), _prefixes(r["required_dx_prefixes"])
                hit = next((p for p in surg if p.startswith(pfx)), None)
                if hit and (not req or any(d.startswith(req) for d in dx_all)):
                    return "postoperative", r["knaus_category"], f"procedure: {hit}"
            return None

        # 1) operations tied to a diagnosis (craniotomy for bleed/tumor, surgery for neoplasm)
        hit = proc_match([r for r in procs if r["required_dx_prefixes"]])
        if hit:
            return hit
        # 2) trauma
        trauma = [(c, k) for c, k in postop if k in ("Multiple trauma", "Head trauma")]
        if trauma:
            c, k = min(trauma, key=lambda x: self._rank("postoperative", x[1]))
            return "postoperative", k, f"principal diagnosis: {c}"
        # 3) other operations (transplant, valve, CABG, spine, vascular)
        hit = proc_match([r for r in procs if not r["required_dx_prefixes"]])
        if hit:
            return hit
        if postop:
            c, k = min(postop, key=lambda x: self._rank("postoperative", x[1]))
            return "postoperative", k, f"principal diagnosis: {c}"
        system = self.chapter(principal_n)
        if system:
            return "postoperative", system, f"ICD chapter of principal diagnosis ({principal_n[:3]})"
        return None, None, "no Knaus category for the principal diagnosis or procedures"


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def apache2(
    stays: pd.DataFrame,
    codes: pd.DataFrame,
    *,
    stay_col: str = "stay_id",
    aps_col: str = "aps",
    age_col: str = "age",
    status_col: str = "operative_status",
    chronic_col: str = "chronic_health",
    knaus_col: str = "knaus_category",
    code_col: str = "code",
    system_col: str = "code_system",
    principal_col: str = "is_principal",
    include_optional_chronic: bool = False,
    unmapped_system: str | None = None,
    surgery_stays=None,
) -> pd.DataFrame:
    """APACHE II score and predicted hospital mortality for each stay.

    stays   One row per stay with columns stay_id, aps (Acute Physiology Score,
            0-60, already calculated), age (years) and operative_status
            ('nonoperative', 'elective' or 'emergency'). Optional overrides:
              chronic_health   TRUE/FALSE instead of deriving it from codes
              knaus_category   a category name from data/knaus_weights.csv
    codes   Long table of ICD codes: stay_id, code, code_system
            ('ICD-10-CM' / 'ICD-10-PCS'), is_principal.

    include_optional_chronic  Also count the borderline chronic-health codes
                              (any heart failure, COPD/emphysema/ILD, myeloma,
                              Z79.899) marked optional in chronic_health_codes.csv.
    unmapped_system           Organ system to use when no Knaus category fits
                              (e.g. 'Metabolic/renal'); default leaves the
                              weight and mortality empty.
    surgery_stays             Stay ids that had an operation, from OR data; if
                              given it replaces the ICD-10-PCS surgery check
                              for the "surgery for" diagnosis categories.
    """
    weights = load_weights()
    picker = _KnausPicker(weights)
    kw = dict(stay_col=stay_col, code_col=code_col, system_col=system_col)

    # chronic health from codes
    ch_cats = load_categories(DATA_DIR / "chronic_health_categories.csv")
    ch_map = load_code_map(DATA_DIR / "chronic_health_codes.csv", include_optional=include_optional_chronic)
    ch_flags = flag_stays(codes, level="category", code_map=ch_map, categories=ch_cats,
                          principal_col=principal_col, **kw)
    arf = flag_stays(codes, principal_col=principal_col, **kw)
    arf = arf["Acute renal failure"] if "Acute renal failure" in arf else pd.Series(False, index=arf.index)

    # diagnosis categories of the principal diagnosis
    pc = principal_categories(codes, principal_col=principal_col, surgery_stays=surgery_stays, **kw)
    cats_by_stay = pc.groupby(stay_col)["category"].apply(list).to_dict()
    is_pr = _bool(codes[principal_col])
    principal = (codes[is_pr & (codes[system_col] == CM)].drop_duplicates(stay_col)
                 .set_index(stay_col)[code_col].map(normalize).to_dict())
    dx_all = codes[codes[system_col] == CM].assign(_n=lambda d: d[code_col].map(normalize)).groupby(stay_col)["_n"].apply(list).to_dict()
    pcs_all = codes[codes[system_col] == PCS].assign(_n=lambda d: d[code_col].map(normalize)).groupby(stay_col)["_n"].apply(list).to_dict()

    out = []
    for rec in stays.to_dict("records"):
        stay = rec[stay_col]
        status = operative_status(rec[status_col])
        aps, age = rec[aps_col], rec[age_col]

        # chronic health
        if chronic_col in rec and pd.notna(rec[chronic_col]) and str(rec[chronic_col]).strip() != "":
            has_ch = str(rec[chronic_col]).strip().upper() in ("TRUE", "1", "Y", "YES", "T")
            ch_source, ch_conditions = "input", ""
        else:
            row = ch_flags.loc[stay] if stay in ch_flags.index else pd.Series(dtype=bool)
            conds = [c for c, v in row.items() if v]
            has_ch, ch_source, ch_conditions = bool(conds), "codes", "; ".join(conds)
        ch_pts = chronic_health_points(has_ch, status)

        score = None
        if pd.notna(aps) and pd.notna(age):
            score = float(aps) + age_points(float(age)) + ch_pts

        # diagnostic category
        override = rec.get(knaus_col) if knaus_col in rec else None
        if override is not None and pd.notna(override) and str(override).strip():
            k = str(override).strip()
            setting = "postoperative" if status != NONOPERATIVE else "nonoperative"
            if (setting, k) not in picker.w.index and ("nonoperative", k) in picker.w.index and k in picker.nonop_always:
                setting = "nonoperative"
            if (setting, k) not in picker.w.index:
                raise ValueError(f"stay {stay}: knaus_category {k!r} is not a {setting} category in knaus_weights.csv")
            source = "input"
        else:
            setting, k, source = picker.pick(status, cats_by_stay.get(stay, []), principal.get(stay, ""),
                                             dx_all.get(stay, []), pcs_all.get(stay, []))
            if k is None and unmapped_system:
                setting = "postoperative" if status != NONOPERATIVE else "nonoperative"
                k, source = unmapped_system, f"unmapped_system ({source})"

        weight = picker.weight(setting, k) if k else None
        emerg = EMERGENCY_SURGERY_COEF if status == EMERGENCY else 0.0
        logit = mort = None
        if score is not None and weight is not None:
            logit = INTERCEPT + SCORE_COEF * score + emerg + weight
            mort = 1.0 / (1.0 + math.exp(-logit))

        out.append({
            stay_col: stay, "aps": aps, "age": age, "operative_status": status,
            "age_points": age_points(float(age)) if pd.notna(age) else None,
            "chronic_health": has_ch, "chronic_health_conditions": ch_conditions,
            "chronic_health_source": ch_source, "chronic_health_points": ch_pts,
            "apache2_score": score,
            "knaus_setting": setting, "knaus_category": k, "knaus_source": source,
            "dx_weight": weight, "emergency_surgery_term": emerg,
            "logit": logit, "predicted_mortality": mort,
            "acute_renal_failure_coded": bool(arf.get(stay, False)),
        })
    return pd.DataFrame(out)
