# APACHE II diagnosis ↔ ICD-10 crosswalk

Code lists and a small Python matcher that map **ICD-10-CM diagnosis** and
**ICD-10-PCS procedure** codes to APACHE admission-diagnosis categories
(cardiovascular, stable angina, aortic and other aneurysms, ARDS, and
toxicology/overdose). Use them to:

- build **yes/no flags** for a stay (e.g. "diagnosed with ARDS during the ICU stay"), or
- assign **one APACHE admission diagnosis** per ICU stay from the principal diagnosis.

> **Status: not validated.** These code lists were compiled from ICD-10-CM/PCS
> code structure and APACHE category wording. They are not an official or
> published APACHE crosswalk. Check them against the ICD-10 code set for your
> data's fiscal years, and review a sample of charts, before using them in
> analyses.

## Repository layout

```
data/
  categories.csv     one row per APACHE category: group, priority, matching rule, notes
  code_map.csv       one row per ICD code or code range mapped to a category
apache_dx/
  match.py           matching functions (flag_stays, assign_admission_dx)
examples/
  example_codes.csv  toy input
tests/
  test_match.py      pytest suite covering the matching rules
```

The CSVs are the master copy. Edit them directly to add, drop or move codes;
the Python code has no codes hard-coded in it.

## Data dictionary

### `data/categories.csv`

| Column | Meaning |
|---|---|
| `group` | Flag the category rolls up to: `Cardiovascular`, `Angina, stable`, `Aortic aneurysm`, `Aneurysm, other`, `ARDS`, `Toxicology` |
| `priority` | Tie-breaker when one stay matches several categories. Lower wins. Only used by `assign_admission_dx`. |
| `category` | APACHE admission diagnosis, worded as on the APACHE form |
| `rule` | How codes combine (see below) |
| `required_dx_prefixes` | For `procedure_with_required_dx`: diagnosis prefixes (pipe-separated) that must appear on the stay |
| `notes` | Coding caveats and judgement calls |

**Rules**

| `rule` | Category is met when… |
|---|---|
| `any` | any listed code is present |
| `dx_and_procedure` | at least one listed ICD-10-CM code **and** one listed ICD-10-PCS code (VSD repair, ventricular aneurysm repair) |
| `procedure_with_required_dx` | a listed ICD-10-PCS code is present **and** the stay has a diagnosis starting with `required_dx_prefixes` (aortic/other aneurysm repair needs `I71` or `I72`) |
| `needs_external_data` | codes alone can't decide it; you pass in the stay ids (MI admitted > 24 h after ischemia onset needs symptom-onset time) |
| `residual` | catch-all; a code counts here only if **no other category** matched it (`Cardiovascular / vascular medical, other`, `Overdose, other toxin, poison or drug`) |

### `data/code_map.csv`

| Column | Meaning |
|---|---|
| `group`, `priority`, `category` | Same as `categories.csv` |
| `code` | ICD code as usually written (with dot), or a range such as `T36-T50` |
| `code_system` | `ICD-10-CM`, `ICD-10-PCS`, or `ICD-10 (WHO)` (WHO-only codes such as X60–X69, ignored unless your data uses that system) |
| `match_type` | `Exact` = that code only · `Prefix` = the code and all children (`I33` matches `I33.0`, `I33.9`) · `Range` = any code whose 3-character category falls in the range |
| `intent_chars` | For poisoning codes T36–T65: allowed **intent** characters (see below). Blank = any |
| `last_char_in` | Allowed final characters. Used to separate stable angina (`1, 2, 8, 9`) from unstable (`0`) in the I25.7x families |
| `optional` | `TRUE` for borderline codes; drop them with `include_optional=False` |
| `description` | Short code description |

## Coding rules worth knowing

**Poisoning intent (T36–T65).** The intent is the last non-`X` character
before the 7th (encounter) character. Usually that's the 6th character
(`T39.012A`), but some codes put it 5th with an `X` placeholder (`T58.01XA`).

| Intent | Meaning | Used by |
|---|---|---|
| 1 | accidental | overdose categories |
| 2 | intentional self-harm | overdose categories, `Overdose, self-inflicted` |
| 3 | assault | overdose categories |
| 4 | undetermined | overdose categories |
| 5 | adverse effect (therapeutic use) | `Toxicity, drug` only |
| 6 | underdosing | none |

**Encounter character.** S and T codes end in A (initial), D (subsequent) or
S (sequela). By default only `A` matches (`initial_encounter_only=True`).

**Unstable vs. stable angina.** When coronary atherosclerosis and angina
coexist, coders must use a combination code (I25.11x, I25.7xx), so many
angina stays never carry I20.x. The last character tells them apart:
`0` = unstable, `1/2/8/9` = stable/other.

**Known limits**

- *MI > 24 h after onset* shares I21/I22 with acute MI. Pass the late-MI stays via `external=`; otherwise they fall into `Infarction, acute myocardial (MI)`.
- *Lithium* codes to T43.59, the same code as atypical antipsychotics, so it lands in the sedatives/antipsychotics category. Use drug names or lithium levels to pull it out.
- *Thoracoabdominal aortic aneurysms* (I71.5, I71.6) aren't an APACHE option; they're mapped to thoracic.
- *Self-inflicted overdose* comes after the agent-specific categories in priority. Lower its priority number in both CSVs if intent should win.
- Some codes only exist from FY2022–FY2024 (aortic site detail, F1x.13 withdrawal, I20.2). Prefix matching still catches the older parent codes.
- In flag mode, the `Cardiovascular` group includes the residual `Cardiovascular / vascular medical, other`, so it turns on for nearly any circulatory code (for example I10 hypertension). Use `level="category"` if you need the specific categories without the catch-all.

## Usage

Requires Python ≥ 3.9 and pandas.

```bash
pip install -r requirements.txt
```

Input is a long table, one row per code per stay:

| stay_id | code | code_system | is_principal |
|---|---|---|---|
| 1 | T39.012A | ICD-10-CM | TRUE |
| 5 | I71.4 | ICD-10-CM | TRUE |
| 5 | 04R00JZ | ICD-10-PCS | FALSE |

Codes may be written with or without dots. `code_system` is required because
some PCS and CM codes look alike.

```python
import pandas as pd
from apache_dx import flag_stays, assign_admission_dx

codes = pd.read_csv("examples/example_codes.csv")

# 1) Yes/no flags using every code on the stay
flags = flag_stays(codes)                      # one column per group
detail = flag_stays(codes, level="category")   # one column per APACHE category

# 2) One APACHE admission diagnosis per stay
#    (principal diagnosis + all procedures, lowest priority wins)
adm = assign_admission_dx(codes)

# Options
flag_stays(codes, include_optional=False)       # drop borderline codes
flag_stays(codes, initial_encounter_only=False) # keep D/S encounters
flag_stays(codes, external={
    "MI admitted > 24 hrs after onset of ischemia": {4, 17, 23},  # stay ids from your timing data
})
```

Output of `flag_stays` on the example file:

```
group    Cardiovascular  Angina, stable  Aortic aneurysm  Aneurysm, other   ARDS  Toxicology
stay_id
1                 False           False            False            False  False        True
2                 False           False            False            False  False        True
3                  True           False            False            False  False        True
4                  True            True            False            False   True       False
5                 False           False             True            False  False       False
...
```

Column names can be changed with `stay_col=`, `code_col=`, `system_col=` and
`principal_col=`.

## Tests

```bash
pip install pytest
pytest
```

## Background and related work

- Knaus WA, Draper EA, Wagner DP, Zimmerman JE. APACHE II: a severity of disease classification system. *Crit Care Med.* 1985;13(10):818–829.
- Kao E, Gulbis B. *Validation of ICD-9-CM/ICD-10-CM Codes for Automated Electronic Scoring of APACHE II, APACHE III, and SAPS II* (supplemental material, 2016). ICD lists for the APACHE II chronic-health comorbidities: https://rstudio-pubs-static.s3.amazonaws.com/231351_940f14aa51a6427a9e92d5a04daefc3e.html
- `icuriskr` (R): https://github.com/bgulbis/icuriskr
- MIT-LCP `mimic-code` (SQL for SAPS II / APS III, including surgical-status flags): https://github.com/MIT-LCP/mimic-code

This repository covers APACHE **admission diagnosis categories**. It does not
cover the APACHE II chronic-health comorbidity points; see Kao & Gulbis for those.
