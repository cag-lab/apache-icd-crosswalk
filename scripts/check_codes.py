"""Check every code in data/ against a CMS ICD-10-CM (and optionally ICD-10-PCS) release.

    python scripts/check_codes.py --cm icd10cm-order-April-1-2026.txt \
                                  [--pcs icd10pcs_order_2026.txt]

The order files are the fixed-width "order" files CMS publishes with each
release (code in columns 7-13, billable flag in column 15). The check fails if:

* an Exact code isn't a billable code in the release (it would never match billed data),
* a Prefix, Range endpoint or rule prefix matches no billable code,
* a last_char_in filter leaves nothing to match.

Exit status is 1 when anything fails, so this can run in CI once the files are
available.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def load_order(path: Path) -> tuple[set[str], list[str]]:
    """(all codes incl. headers, sorted billable codes)."""
    every, billable = set(), []
    for line in path.read_text(encoding="latin-1").splitlines():
        if len(line) < 15:
            continue
        code = line[6:13].strip()
        every.add(code)
        if line[14] == "1":
            billable.append(code)
    return every, sorted(billable)


def norm(code: str) -> str:
    return code.strip().upper().replace(".", "")


def rows(name: str) -> list[dict]:
    with open(DATA / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Release:
    def __init__(self, path: Path):
        self.every, self.billable = load_order(path)
        self.billable_set = set(self.billable)

    def children(self, prefix: str) -> list[str]:
        return [c for c in self.billable if c.startswith(prefix)]


def check(cm: Release, pcs: Release | None) -> list[str]:
    errors: list[str] = []
    releases = {"ICD-10-CM": cm, "ICD-10-PCS": pcs}

    def need(rel: Release, where: str, prefix: str):
        if prefix and not rel.children(prefix):
            errors.append(f"{where}: {prefix} matches no billable code")

    for name in ("code_map.csv", "chronic_health_codes.csv"):
        for i, r in enumerate(rows(name), 2):
            rel = releases.get(r["code_system"])
            if rel is None:  # WHO-only codes, or PCS when no PCS file was given
                continue
            where = f"{name}:{i} [{r['category']}]"
            mt = r["match_type"]
            if mt == "Range":
                a, b = (norm(x) for x in r["code"].split("-"))
                for end in (a, b):
                    if end not in rel.every:
                        errors.append(f"{where}: range endpoint {end} is not in the release")
                matched = [c for c in rel.billable if a <= c[:3] <= b]
            elif mt == "Exact":
                c = norm(r["code"])
                if c not in rel.every:
                    errors.append(f"{where}: {c} is not in the release")
                    continue
                if c not in rel.billable_set:
                    errors.append(f"{where}: Exact {c} is a header, not a billable code")
                    continue
                matched = [c]
            else:
                matched = rel.children(norm(r["code"]))
                if not matched:
                    errors.append(f"{where}: Prefix {norm(r['code'])} matches no billable code")
                    continue
            last = r.get("last_char_in", "")
            if last and not any(m[-1] in last for m in matched):
                errors.append(f"{where}: last_char_in {last} leaves no codes")

    for name in ("categories.csv", "chronic_health_categories.csv"):
        for i, r in enumerate(rows(name), 2):
            for col in ("required_dx_prefixes", "exclude_dx_prefixes"):
                for p in r[col].split("|"):
                    need(cm, f"{name}:{i} {col}", norm(p))
            if pcs:
                for p in r["surgery_pcs_prefixes"].split("|"):
                    need(pcs, f"{name}:{i} surgery_pcs_prefixes", norm(p))
    for i, r in enumerate(rows("trauma_regions.csv"), 2):
        need(cm, f"trauma_regions.csv:{i}", norm(r["prefix"]))
    for i, r in enumerate(rows("knaus_dx_map.csv"), 2):
        for p in r["code_prefixes"].split("|"):
            need(cm, f"knaus_dx_map.csv:{i}", norm(p))
    for i, r in enumerate(rows("knaus_postop_procedures.csv"), 2):
        for p in r["required_dx_prefixes"].split("|"):
            need(cm, f"knaus_postop_procedures.csv:{i} required_dx_prefixes", norm(p))
        if pcs:
            for p in r["pcs_prefixes"].split("|"):
                need(pcs, f"knaus_postop_procedures.csv:{i} pcs_prefixes", norm(p))
    if pcs:
        for i, r in enumerate(rows("surgery_exclusions.csv"), 2):
            need(pcs, f"surgery_exclusions.csv:{i}", norm(r["pcs_prefix"]))
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cm", required=True, type=Path, help="ICD-10-CM order file")
    ap.add_argument("--pcs", type=Path, help="ICD-10-PCS order file (optional)")
    args = ap.parse_args(argv)
    cm = Release(args.cm)
    pcs = Release(args.pcs) if args.pcs else None
    errors = check(cm, pcs)
    for e in errors:
        print(e)
    checked = "ICD-10-CM" + (" and ICD-10-PCS" if pcs else "")
    print(f"{len(errors)} problem(s); {checked} checked "
          f"({len(cm.billable)} billable CM codes"
          + (f", {len(pcs.billable)} billable PCS codes" if pcs else "") + ")",
          file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
