"""Match ICD-10-CM / ICD-10-PCS codes to APACHE diagnosis categories.

Input is a long table with one row per (stay, code):

    stay_id | code     | code_system | is_principal
    101     | T39.012A | ICD-10-CM   | True
    101     | I50.9    | ICD-10-CM   | False
    102     | 04R00JZ  | ICD-10-PCS  | False

Two entry points:

* ``flag_stays``           -> one True/False column per group (or category),
                              using every code on the stay.
* ``assign_admission_dx``  -> one APACHE admission diagnosis per stay, using
                              the principal diagnosis (plus procedures) and
                              the lowest priority number.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CM, PCS = "ICD-10-CM", "ICD-10-PCS"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def normalize(code: str) -> str:
    """Upper-case and strip dots/whitespace: 'T39.012A' -> 'T39012A'."""
    return str(code).strip().upper().replace(".", "")


def load_categories(path: str | Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "categories.csv", dtype=str, keep_default_na=False)
    df["priority"] = df["priority"].astype(int)
    return df


def load_code_map(path: str | Path | None = None, include_optional: bool = True) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "code_map.csv", dtype=str, keep_default_na=False)
    df["priority"] = df["priority"].astype(int)
    df["optional"] = df["optional"].str.upper().eq("TRUE")
    if not include_optional:
        df = df[~df["optional"]]
    starts, ends = [], []
    for code, mt in zip(df["code"], df["match_type"]):
        if mt == "Range":
            a, b = code.split("-")
            starts.append(normalize(a))
            ends.append(normalize(b))
        else:
            starts.append(normalize(code))
            ends.append("")
    df["start_n"], df["end_n"] = starts, ends
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Single-code matching
# --------------------------------------------------------------------------- #
def intent_char(code_n: str) -> str | None:
    """Intent character of a T36-T65 poisoning code (dotless).

    Intent is the last non-'X' character among positions 4-6, e.g.
    T39012A -> '2', T510X1A -> '1', T5801XA -> '1' (intent in position 5).
    1 accidental, 2 self-harm, 3 assault, 4 undetermined,
    5 adverse effect, 6 underdosing.
    """
    core = code_n[3:6].replace("X", "")
    return core[-1] if core else None


def _is_poisoning(code_n: str) -> bool:
    return len(code_n) >= 3 and "T36" <= code_n[:3] <= "T65"


def row_matches(code_n: str, system: str, row) -> bool:
    if system != row.code_system:
        return False
    mt = row.match_type
    if mt == "Exact":
        ok = code_n == row.start_n
    elif mt == "Prefix":
        ok = code_n.startswith(row.start_n)
    elif mt == "Range":
        ok = row.start_n <= code_n[:3] <= row.end_n
    else:
        raise ValueError(f"Unknown match_type {mt!r}")
    if not ok:
        return False
    if row.intent_chars and _is_poisoning(code_n):
        ic = intent_char(code_n)
        if ic is None or ic not in row.intent_chars:
            return False
    if row.last_char_in and code_n[-1] not in row.last_char_in:
        return False
    return True


# --------------------------------------------------------------------------- #
# Table matching
# --------------------------------------------------------------------------- #
def match_codes(
    codes: pd.DataFrame,
    code_map: pd.DataFrame | None = None,
    categories: pd.DataFrame | None = None,
    *,
    stay_col: str = "stay_id",
    code_col: str = "code",
    system_col: str = "code_system",
    initial_encounter_only: bool = True,
) -> pd.DataFrame:
    """Return one row per (input row, matched category).

    Residual categories (rule == 'residual') only keep a code when no
    non-residual category matched that same code.
    """
    code_map = load_code_map() if code_map is None else code_map
    categories = load_categories() if categories is None else categories
    residual = set(categories.loc[categories["rule"] == "residual", "category"])

    df = codes[[stay_col, code_col, system_col]].copy()
    df["_code_n"] = df[code_col].map(normalize)

    rows = list(code_map.itertuples(index=False))
    lookup: dict[tuple[str, str], list[tuple]] = {}
    for code_n, system in df[["_code_n", system_col]].drop_duplicates().itertuples(index=False):
        # S/T chapter codes carry an encounter character in position 7.
        if (initial_encounter_only and system == CM and code_n[:1] in ("S", "T")
                and len(code_n) == 7 and code_n[6] != "A"):
            lookup[(code_n, system)] = []
            continue
        hits = [r for r in rows if row_matches(code_n, system, r)]
        if any(r.category not in residual for r in hits):
            hits = [r for r in hits if r.category not in residual]
        lookup[(code_n, system)] = [(r.group, r.priority, r.category) for r in hits]

    out = []
    for stay, code, system, code_n in df[[stay_col, code_col, system_col, "_code_n"]].itertuples(index=False):
        for group, priority, category in lookup[(code_n, system)]:
            out.append((stay, code, system, group, priority, category))
    return pd.DataFrame(out, columns=[stay_col, code_col, system_col, "group", "priority", "category"])


def _apply_rules(
    matches: pd.DataFrame,
    codes: pd.DataFrame,
    categories: pd.DataFrame,
    *,
    stay_col: str,
    code_col: str,
    system_col: str,
    external: Mapping[str, Iterable] | None,
) -> pd.DataFrame:
    """Collapse code-level matches to (stay, category) pairs that satisfy each category's rule."""
    rule = dict(zip(categories["category"], categories["rule"]))
    req = dict(zip(categories["category"], categories["required_dx_prefixes"]))
    external = {k: set(v) for k, v in (external or {}).items()}

    dx_by_stay = (
        codes.loc[codes[system_col] == CM]
        .assign(_n=lambda d: d[code_col].map(normalize))
        .groupby(stay_col)["_n"].apply(list).to_dict()
    )

    keep = []
    for (stay, category), grp in matches.groupby([stay_col, "category"], sort=False):
        r = rule.get(category, "any")
        systems = set(grp[system_col])
        if r == "dx_and_procedure":
            ok = CM in systems and PCS in systems
        elif r == "procedure_with_required_dx":
            prefixes = tuple(normalize(p) for p in req[category].split("|") if p)
            ok = PCS in systems and any(c.startswith(prefixes) for c in dx_by_stay.get(stay, []))
        elif r == "needs_external_data":
            ok = stay in external.get(category, set())
        else:  # any, residual
            ok = True
        if ok:
            keep.append((stay, grp["group"].iloc[0], grp["priority"].iloc[0], category))
    return pd.DataFrame(keep, columns=[stay_col, "group", "priority", "category"])


def flag_stays(
    codes: pd.DataFrame,
    *,
    level: str = "group",
    include_optional: bool = True,
    initial_encounter_only: bool = True,
    external: Mapping[str, Iterable] | None = None,
    stay_col: str = "stay_id",
    code_col: str = "code",
    system_col: str = "code_system",
    code_map: pd.DataFrame | None = None,
    categories: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per stay, one boolean column per group (or per category).

    ``external`` maps a category name to stay ids that meet a condition codes
    can't capture, e.g. {"MI admitted > 24 hrs after onset of ischemia": {...}}.
    """
    if level not in ("group", "category"):
        raise ValueError("level must be 'group' or 'category'")
    code_map = load_code_map(include_optional=include_optional) if code_map is None else code_map
    categories = load_categories() if categories is None else categories
    kw = dict(stay_col=stay_col, code_col=code_col, system_col=system_col)
    m = match_codes(codes, code_map, categories, initial_encounter_only=initial_encounter_only, **kw)
    hits = _apply_rules(m, codes, categories, external=external, **kw)

    labels = categories[level].drop_duplicates().tolist()
    stays = pd.Index(codes[stay_col].drop_duplicates(), name=stay_col)
    wide = pd.crosstab(hits[stay_col], hits[level]).gt(0) if len(hits) else pd.DataFrame()
    return wide.reindex(index=stays, columns=labels, fill_value=False).astype(bool)


def assign_admission_dx(
    codes: pd.DataFrame,
    *,
    principal_col: str = "is_principal",
    include_optional: bool = True,
    initial_encounter_only: bool = True,
    external: Mapping[str, Iterable] | None = None,
    stay_col: str = "stay_id",
    code_col: str = "code",
    system_col: str = "code_system",
    code_map: pd.DataFrame | None = None,
    categories: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One APACHE admission diagnosis per stay.

    Uses the principal ICD-10-CM diagnosis plus all ICD-10-PCS procedures,
    then keeps the matched category with the lowest priority number.
    Stays with no match get NaN.
    """
    code_map = load_code_map(include_optional=include_optional) if code_map is None else code_map
    categories = load_categories() if categories is None else categories
    principal = codes[principal_col].astype(str).str.upper().isin(["TRUE", "1", "Y", "YES"])
    subset = codes[(principal & (codes[system_col] == CM)) | (codes[system_col] == PCS)]
    kw = dict(stay_col=stay_col, code_col=code_col, system_col=system_col)
    m = match_codes(subset, code_map, categories, initial_encounter_only=initial_encounter_only, **kw)
    # required-dx checks may look at any diagnosis on the stay, so pass the full table
    hits = _apply_rules(m, codes, categories, external=external, **kw)
    best = hits.sort_values("priority").drop_duplicates(stay_col)
    stays = pd.DataFrame({stay_col: codes[stay_col].drop_duplicates()})
    return stays.merge(best, on=stay_col, how="left")
