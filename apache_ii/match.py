"""Match ICD-10-CM / ICD-10-PCS codes to APACHE diagnosis categories.

Input is a long table with one row per (stay, code):

    stay_id | code     | code_system | is_principal
    101     | T39.012A | ICD-10-CM   | True
    101     | I50.9    | ICD-10-CM   | False
    102     | 04R00JZ  | ICD-10-PCS  | False

Two entry points:

* ``flag_stays``            -> one True/False column per group (or category),
                               using every code on the stay.
* ``principal_categories``  -> the categories matched by the principal
                               diagnosis (plus procedures), one row each.
                               ``apache_ii.score`` uses this to pick the
                               Knaus diagnostic category.

All code lists live in ``data/``; this module only holds the matching logic.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CM, PCS = "ICD-10-CM", "ICD-10-PCS"
TRUE_STRINGS = {"TRUE", "1", "Y", "YES", "T"}

# When a stay has injuries in 3+ regions and the principal diagnosis isn't an
# injury, the first region in this list present is treated as the primary one.
REGION_HIERARCHY = ["head", "spinal", "chest", "abdomen", "pelvis", "face", "extremity"]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def normalize(code: str) -> str:
    """Upper-case and strip dots/whitespace: 'T39.012A' -> 'T39012A'."""
    return str(code).strip().upper().replace(".", "")


def _bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.upper().isin(TRUE_STRINGS)


def _prefixes(cell: str) -> tuple[str, ...]:
    return tuple(normalize(p) for p in str(cell).split("|") if p.strip())


def load_categories(path: str | Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "categories.csv", dtype=str, keep_default_na=False)
    for col in ("requires_surgery", "catch_all"):
        df[col] = _bool(df[col]) if col in df else False
    for col in ("required_dx_prefixes", "exclude_dx_prefixes", "surgery_pcs_prefixes", "trauma_pattern"):
        if col not in df:
            df[col] = ""
    return df


def load_code_map(path: str | Path | None = None, include_optional: bool = True) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "code_map.csv", dtype=str, keep_default_na=False)
    df["optional"] = _bool(df["optional"])
    df["exclude"] = _bool(df["exclude"]) if "exclude" in df else False
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


def load_trauma_regions(path: str | Path | None = None) -> list[tuple[str, str]]:
    """(dotless prefix, region) pairs, longest prefix first."""
    df = pd.read_csv(path or DATA_DIR / "trauma_regions.csv", dtype=str, keep_default_na=False)
    pairs = [(normalize(p), r) for p, r in zip(df["prefix"], df["region"])]
    return sorted(pairs, key=lambda x: -len(x[0]))


def load_surgery_exclusions(path: str | Path | None = None) -> tuple[str, ...]:
    df = pd.read_csv(path or DATA_DIR / "surgery_exclusions.csv", dtype=str, keep_default_na=False)
    return tuple(normalize(p) for p in df["pcs_prefix"])


# --------------------------------------------------------------------------- #
# Single-code helpers
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


# 7th characters that mark a subsequent encounter or sequela. Initial
# encounters are A, plus B and C for open fractures (e.g. S72.001B). Some
# 7-character T codes (T86.84xx, corneal transplant) end in a laterality digit,
# not an encounter character, and are left alone.
NON_INITIAL_7TH = frozenset("DEFGHJKMNPQRS")


def _non_initial_encounter(code_n: str, system: str) -> bool:
    """S/T chapter codes carry an encounter character in position 7."""
    return (
        system == CM and code_n[:1] in ("S", "T") and len(code_n) == 7
        and code_n[6] in NON_INITIAL_7TH
    )


def is_surgical(code_n: str, exclusions: tuple[str, ...] = ()) -> bool:
    """ICD-10-PCS Medical and Surgical section, open (0) or percutaneous
    endoscopic (4) approach, not on the bedside-procedure exclusion list."""
    return (
        len(code_n) == 7
        and code_n[0] == "0"
        and code_n[4] in ("0", "4")
        and not code_n.startswith(exclusions)
    )


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


def trauma_region(code_n: str, regions: list[tuple[str, str]]) -> str | None:
    for prefix, region in regions:
        if code_n.startswith(prefix):
            return region
    return None


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
    """Return one row per (input row, matched category), from code_map.csv.

    * Rows with ``exclude=TRUE`` remove that category for the codes they match.
    * Residual categories (rule == 'residual') only keep a code when no
      non-residual category matched that same code.
    Trauma-pattern and surgery-only categories are not code-based; see
    ``structural_hits``.
    """
    code_map = load_code_map() if code_map is None else code_map
    categories = load_categories() if categories is None else categories
    residual = set(categories.loc[categories["rule"] == "residual", "category"])
    meta = categories.set_index("category")[["group"]].to_dict("index")

    df = codes[[stay_col, code_col, system_col]].copy()
    df["_code_n"] = df[code_col].map(normalize)

    # Index rows by (code system, first character); every range starts and
    # ends with the same letter, so this never drops a candidate.
    by_first: dict[tuple[str, str], list] = defaultdict(list)
    for r in code_map.itertuples(index=False):
        by_first[(r.code_system, r.start_n[:1])].append(r)
    lookup: dict[tuple[str, str], list[str]] = {}
    for code_n, system in df[["_code_n", system_col]].drop_duplicates().itertuples(index=False):
        if initial_encounter_only and _non_initial_encounter(code_n, system):
            lookup[(code_n, system)] = []
            continue
        incl, excl = set(), set()
        for r in by_first.get((system, code_n[:1]), ()):
            if row_matches(code_n, system, r):
                (excl if r.exclude else incl).add(r.category)
        hits = incl - excl
        if hits - residual:
            hits -= residual
        lookup[(code_n, system)] = sorted(hits)

    out = []
    for stay, code, system, code_n in df[[stay_col, code_col, system_col, "_code_n"]].itertuples(index=False):
        for category in lookup[(code_n, system)]:
            out.append((stay, code, system, meta[category]["group"], category))
    return pd.DataFrame(out, columns=[stay_col, code_col, system_col, "group", "category"])


class _StayIndex:
    """Per-stay lookups used by the category rules."""

    def __init__(self, codes, *, stay_col, code_col, system_col, principal_col,
                 initial_encounter_only, regions, surgery_exclusions):
        self.dx = defaultdict(list)          # stay -> all CM codes (normalized)
        self.surg = defaultdict(list)        # stay -> surgical PCS codes
        self.regions = defaultdict(set)      # stay -> injured body regions
        self.principal_region = {}           # stay -> region of principal dx
        self.principal_injury = set()        # stays whose principal dx maps to a region
        has_principal = principal_col in codes
        cols = [stay_col, code_col, system_col] + ([principal_col] if has_principal else [])
        for rec in codes[cols].itertuples(index=False):
            stay, code, system = rec[0], rec[1], rec[2]
            principal = has_principal and str(rec[3]).strip().upper() in TRUE_STRINGS
            n = normalize(code)
            if system == CM:
                self.dx[stay].append(n)
                if initial_encounter_only and _non_initial_encounter(n, system):
                    continue
                region = trauma_region(n, regions)
                if region:
                    self.regions[stay].add(region)
                    if principal:
                        self.principal_region[stay] = region
                        self.principal_injury.add(stay)
            elif system == PCS and is_surgical(n, surgery_exclusions):
                self.surg[stay].append(n)

    def has_dx(self, stay, prefixes) -> bool:
        return any(c.startswith(prefixes) for c in self.dx.get(stay, ()))

    def has_surgery(self, stay, prefixes) -> bool:
        s = self.surg.get(stay, ())
        return any(c.startswith(prefixes) for c in s) if prefixes else bool(s)

    def trauma_pattern(self, stay) -> frozenset | None:
        regs = self.regions.get(stay)
        if not regs:
            return None
        if len(regs) <= 2:
            return frozenset(regs)
        primary = self.principal_region.get(stay) or next(r for r in REGION_HIERARCHY if r in regs)
        return frozenset({primary, "multiple"})


def _resolve(
    matches: pd.DataFrame,
    codes: pd.DataFrame,
    categories: pd.DataFrame,
    *,
    stay_col: str,
    code_col: str,
    system_col: str,
    principal_col: str,
    initial_encounter_only: bool,
    external: Mapping[str, Iterable] | None,
    surgery_stays: Iterable | None,
    require_principal_injury: bool,
) -> pd.DataFrame:
    """Apply each category's rule; return (stay, group, category) hits."""
    idx = _StayIndex(
        codes, stay_col=stay_col, code_col=code_col, system_col=system_col,
        principal_col=principal_col, initial_encounter_only=initial_encounter_only,
        regions=load_trauma_regions(), surgery_exclusions=load_surgery_exclusions(),
    )
    external = {k: set(v) for k, v in (external or {}).items()}
    surgery_stays = set(surgery_stays) if surgery_stays is not None else None
    cat = categories.set_index("category")

    def surgery_ok(stay, c) -> bool:
        if not cat.at[c, "requires_surgery"]:
            return True
        if surgery_stays is not None:
            return stay in surgery_stays
        return idx.has_surgery(stay, _prefixes(cat.at[c, "surgery_pcs_prefixes"]))

    def exclusions_ok(stay, c) -> bool:
        ex = _prefixes(cat.at[c, "exclude_dx_prefixes"])
        return not (ex and idx.has_dx(stay, ex))

    keep = []
    # 1) code-based categories
    for (stay, c), grp in matches.groupby([stay_col, "category"], sort=False):
        rule = cat.at[c, "rule"]
        systems = set(grp[system_col])
        if rule == "dx_and_procedure":
            ok = CM in systems and PCS in systems
        elif rule == "with_required_dx":
            ok = idx.has_dx(stay, _prefixes(cat.at[c, "required_dx_prefixes"]))
        elif rule == "needs_external_data":
            ok = stay in external.get(c, set())
        else:  # any, residual
            ok = True
        if ok and surgery_ok(stay, c) and exclusions_ok(stay, c):
            keep.append((stay, cat.at[c, "group"], c))

    # 2) structural categories (no code rows): trauma patterns, surgery-only
    stays = codes[stay_col].drop_duplicates()
    trauma = categories[categories["rule"] == "trauma"]
    surg_only = categories[categories["rule"] == "surgery_only"]
    for stay in stays:
        pattern = idx.trauma_pattern(stay)
        if pattern is not None and (not require_principal_injury or stay in idx.principal_injury):
            for r in trauma.itertuples(index=False):
                want = frozenset(p.strip() for p in r.trauma_pattern.split("+"))
                if want == pattern and surgery_ok(stay, r.category) and exclusions_ok(stay, r.category):
                    keep.append((stay, r.group, r.category))
        for r in surg_only.itertuples(index=False):
            if surgery_stays is not None:
                ok = stay in surgery_stays and idx.has_surgery(stay, _prefixes(r.surgery_pcs_prefixes))
            else:
                ok = idx.has_surgery(stay, _prefixes(r.surgery_pcs_prefixes))
            if ok and exclusions_ok(stay, r.category):
                keep.append((stay, r.group, r.category))

    return pd.DataFrame(keep, columns=[stay_col, "group", "category"])


def flag_stays(
    codes: pd.DataFrame,
    *,
    level: str = "group",
    include_optional: bool = True,
    include_catch_all: bool = False,
    initial_encounter_only: bool = True,
    external: Mapping[str, Iterable] | None = None,
    surgery_stays: Iterable | None = None,
    stay_col: str = "stay_id",
    code_col: str = "code",
    system_col: str = "code_system",
    principal_col: str = "is_principal",
    code_map: pd.DataFrame | None = None,
    categories: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per stay, one boolean column per group (or per category).

    include_catch_all  Count the broad "<system> medical, other" categories.
                       Off by default: in flag mode they fire for almost any
                       chronic code in that chapter (e.g. K21.9 reflux).
    external           {category name: stay ids} for conditions codes can't
                       capture, e.g. "MI admitted > 24 hrs after onset of ischemia".
    surgery_stays      Stay ids that had an operation, from your OR data. If
                       given, it replaces the ICD-10-PCS surgery definition.
    """
    if level not in ("group", "category"):
        raise ValueError("level must be 'group' or 'category'")
    code_map = load_code_map(include_optional=include_optional) if code_map is None else code_map
    categories = load_categories() if categories is None else categories
    if not include_catch_all:
        drop = set(categories.loc[categories["catch_all"], "category"])
        code_map = code_map[~code_map["category"].isin(drop)]
    kw = dict(stay_col=stay_col, code_col=code_col, system_col=system_col)
    m = match_codes(codes, code_map, categories, initial_encounter_only=initial_encounter_only, **kw)
    hits = _resolve(
        m, codes, categories, principal_col=principal_col,
        initial_encounter_only=initial_encounter_only, external=external,
        surgery_stays=surgery_stays, require_principal_injury=False, **kw,
    )
    if not include_catch_all:
        hits = hits[~hits["category"].isin(drop)]

    labels = categories.loc[include_catch_all | ~categories["catch_all"], level].drop_duplicates().tolist()
    stays = pd.Index(codes[stay_col].drop_duplicates(), name=stay_col)
    wide = pd.crosstab(hits[stay_col], hits[level]).gt(0) if len(hits) else pd.DataFrame()
    return wide.reindex(index=stays, columns=labels, fill_value=False).astype(bool)


def principal_categories(
    codes: pd.DataFrame,
    *,
    principal_col: str = "is_principal",
    include_optional: bool = True,
    include_catch_all: bool = True,
    initial_encounter_only: bool = True,
    external: Mapping[str, Iterable] | None = None,
    surgery_stays: Iterable | None = None,
    stay_col: str = "stay_id",
    code_col: str = "code",
    system_col: str = "code_system",
    code_map: pd.DataFrame | None = None,
    categories: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Categories matched by each stay's principal diagnosis, one row each.

    Code-based categories match on the principal ICD-10-CM diagnosis plus all
    ICD-10-PCS procedures; required/excluded diagnoses are checked against the
    whole stay. Trauma patterns use every injury code on the stay but only
    apply when the principal diagnosis is itself an injury.
    """
    code_map = load_code_map(include_optional=include_optional) if code_map is None else code_map
    categories = load_categories() if categories is None else categories
    if not include_catch_all:
        drop = set(categories.loc[categories["catch_all"], "category"])
        code_map = code_map[~code_map["category"].isin(drop)]
    principal = _bool(codes[principal_col])
    subset = codes[(principal & (codes[system_col] == CM)) | (codes[system_col] == PCS)]
    kw = dict(stay_col=stay_col, code_col=code_col, system_col=system_col)
    m = match_codes(subset, code_map, categories, initial_encounter_only=initial_encounter_only, **kw)
    hits = _resolve(
        m, codes, categories, principal_col=principal_col,
        initial_encounter_only=initial_encounter_only, external=external,
        surgery_stays=surgery_stays, require_principal_injury=True, **kw,
    )
    if not include_catch_all:
        hits = hits[~hits["category"].isin(drop)]
    return hits.drop_duplicates().reset_index(drop=True)
