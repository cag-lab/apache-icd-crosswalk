# APACHE II diagnosis ↔ ICD-10 crosswalk

Code lists and a small Python matcher that map **ICD-10-CM diagnosis** and
**ICD-10-PCS procedure** codes to APACHE admission-diagnosis categories:
231 categories across 43 groups (cardiovascular, respiratory, neurologic, GI,
genitourinary, infection, metabolic, hematologic, cancer, musculoskeletal,
toxicology and trauma). Use them to:

- build **yes/no flags** for a stay (e.g. "diagnosed with a GI bleed during the ICU stay"), or
- assign **one APACHE admission diagnosis** per ICU stay from the principal diagnosis.

> **Status: not clinically validated.** The lists were built from ICD-10-CM/PCS
> code structure and APACHE category wording. They are not an official or
> published APACHE crosswalk. Every ICD-10-CM code and prefix has been checked
> to exist in the April 2026 ICD-10-CM release, but whether a code *belongs* in
> a category is a judgement call. Review the `notes` column and a sample of
> charts before using these in analyses.

## Repository layout

```
data/
  categories.csv           one row per APACHE category: group, priority, matching rule, notes
  code_map.csv             one row per ICD code / prefix / range mapped to a category
  trauma_regions.csv       ICD-10-CM injury prefixes -> body region (head, face, chest, ...)
  surgery_exclusions.csv   bedside ICD-10-PCS procedures that don't count as "surgery"
apache_dx/
  match.py                 matching functions (flag_stays, assign_admission_dx)
examples/
  example_codes.csv        toy input
tests/
  test_match.py            pytest suite covering the matching rules
```

The CSVs are the master copy. Edit them directly to add, drop or move codes;
the Python code has no codes hard-coded in it.

## Groups (one flag each)

| Group | Categories |
|---|---|
| Cardiovascular | open-heart complications, tamponade, papillary muscle rupture, VSD repair, MI, MI > 24 h, unstable angina, endocarditis, pericarditis, pericardial effusion, myocardial contusion, primary pulmonary hypertension, arterial thrombus, DVT, hemorrhage, hematoma, chest pain (atypical / unknown), hemodynamic monitoring, *CV other* |
| Angina, stable · Aortic aneurysm · Aneurysm, other · ARDS | as named; aneurysm groups include repair procedures |
| Cardiac arrest · Cardiogenic shock · CHF · Hypertension, uncontrolled | CHF includes cardiomyopathy |
| Rhythm, ventricular · Rhythm, atrial/supraventricular · Rhythm, conduction defect | |
| CVA | stroke, intracranial hemorrhage, SAH/aneurysm, SAH/AVM, and the "surgery for" versions |
| Subdural hematoma · Coma · Seizures · Neuro infection · Neuro (medical) | neuro medical: encephalopathy, GBS, myasthenia, ALS, hydrocephalus, epidural hematoma, cranial nerve palsy, neuromuscular, *neuro other* |
| Pulmonary infection · Respiratory arrest · Asthma · Respiratory (medical) | pneumonia by organism type, pulmonary sepsis; respiratory medical: PE, pneumothorax, effusion, hemoptysis, airway obstruction, smoke inhalation, drowning, atelectasis, restrictive disease, sleep apnea, weaning, *respiratory other* |
| GI perforation · GI bleeding · Pancreatitis · GI infection · GI | GI: hepatic encephalopathy/failure, hepatorenal, cholangitis, peritonitis, abscess, obstruction, ischemia, IBD, peptic ulcer, diverticular disease, *GI other* |
| GU infection · Genitourinary | pre-eclampsia, postpartum hemorrhage, renal bleeding, *GU other* |
| Skin/soft tissue infection · Infection, other | sepsis by site; "Sepsis, unknown" = no site coded |
| DKA/HHNC · Metabolic · Hypovolemia · Anaphylaxis | metabolic: thyroid storm, myxedema, Addison's, hypoglycemia, heat/cold, electrolytes, *metabolic other* |
| Hematologic · Hematologic surgery | anemia, neutropenia, thrombocytopenia, sickle crisis, transfusion reaction, coagulopathy |
| Cancer | leukemia (ALL/AML/CLL/CML/other), lymphoma, myeloma/MDS, pancytopenia, solid tumors by site, *cancer other site* |
| Musculoskeletal · Chest pain, epigastric | lupus, vasculitis, RA, scleroderma, rhabdomyolysis, septic arthritis, connective tissue disease, *MSK other* |
| Toxicology | overdoses by agent, poisoning, drug toxicity, self-inflicted, alcohol/drug withdrawal |
| Head trauma · Trauma | body-region combinations, burns, other trauma, and the "surgery for" versions |

*Italic* = catch-all (see below).

## How matching works

### Data dictionary: `categories.csv`

| Column | Meaning |
|---|---|
| `group` | The flag the category rolls up to |
| `priority` | Tie-breaker in admission-diagnosis mode. Lower wins. |
| `category` | APACHE admission diagnosis, worded as on the APACHE form |
| `rule` | How the category is matched (table below) |
| `required_dx_prefixes` | For `with_required_dx`: the stay must also have a diagnosis starting with one of these (pipe-separated) |
| `exclude_dx_prefixes` | The category is dropped if the stay has any diagnosis starting with one of these |
| `requires_surgery` | `TRUE` for "surgery for" categories (see Surgery) |
| `surgery_pcs_prefixes` | ICD-10-PCS prefixes the operation must start with (blank = any operation) |
| `trauma_pattern` | For `rule = trauma`: body regions, e.g. `head+extremity`, `chest+multiple`, `face` |
| `catch_all` | `TRUE` for the broad "<system> medical, other" categories |
| `notes` | Coding caveats and judgement calls |

| `rule` | Category is met when… |
|---|---|
| `any` | any listed code is present |
| `residual` | a listed code is present **and no non-residual category matched that code** (e.g. `Leukemia, other` = a leukemia code not claimed by ALL/AML/CLL/CML) |
| `dx_and_procedure` | a listed ICD-10-CM code **and** a listed ICD-10-PCS code (VSD repair, ventricular aneurysm repair) |
| `with_required_dx` | a listed code **and** a diagnosis from `required_dx_prefixes` on the same stay (sepsis by site, aortic repair, open-heart complications) |
| `needs_external_data` | codes can't decide it; pass the stay ids in (MI > 24 h after onset) |
| `trauma` | the stay's injured body regions equal `trauma_pattern` (see Trauma) |
| `surgery_only` | the stay had an operation starting with `surgery_pcs_prefixes` (hematologic surgery) |

`exclude_dx_prefixes` and `requires_surgery` are applied on top of any rule.

### Data dictionary: `code_map.csv`

| Column | Meaning |
|---|---|
| `group`, `priority`, `category` | Same as `categories.csv` |
| `code` | ICD code as usually written (with dot), or a range such as `T36-T50` |
| `code_system` | `ICD-10-CM`, `ICD-10-PCS`, or `ICD-10 (WHO)` (only matches if your data uses that label) |
| `match_type` | `Exact` = that code only · `Prefix` = the code and all children · `Range` = any code whose 3-character category is in the range |
| `intent_chars` | For poisoning codes T36–T65: allowed intent characters. Blank = any |
| `last_char_in` | Allowed final characters (stable vs. unstable angina) |
| `optional` | `TRUE` for borderline codes; drop them with `include_optional=False` |
| `exclude` | `TRUE` = this row *removes* the category for codes it matches (e.g. Anemia `D50-D64` minus `D61.81` pancytopenia) |
| `description` | Short code description |

### Catch-all categories

The "medical, other" categories (`Cardiovascular / vascular medical, other`,
`Neurologic medical, other`, `Respiratory - medical, other`, `GI medical, other`,
`Genitourinary medical, other`, `Metabolic/endocrine medical, other`,
`Musculoskeletal medical, other`) pick up every leftover code in their ICD
chapter. That's what APACHE intends for a *principal* diagnosis, but as yes/no
flags they'd turn on for almost anyone (reflux, hyperlipidemia, COPD, AKI).

- `flag_stays` leaves them **out** by default (`include_catch_all=True` to add them).
- `assign_admission_dx` keeps them **in** by default.

Non-catch-all residuals (`Leukemia, other`, `Overdose, other toxin`, `Cancer, other site`) are always on.

### Sepsis by site

ICD-10-CM codes sepsis (A40/A41, R65.2, …) separately from the site of
infection. Each "Sepsis, <site>" category needs **a sepsis code plus a
site-of-infection code** on the same stay (pneumonia → pulmonary, UTI or
pyelonephritis → renal/UTI, peritonitis or C. difficile → GI, and so on).
`Sepsis, unknown` = a sepsis code with **no** site code. A stay with several
sites gets several sepsis flags. In admission-diagnosis mode the sepsis code is
usually principal, so the order is pulmonary, GI, renal/UTI, gynecologic,
skin/soft tissue, other, unknown.

### Surgery ("surgery for" categories)

By default, a stay counts as having surgery when it has an ICD-10-PCS code that:

- is in the Medical and Surgical section (first character `0`),
- uses an open (`0`) or percutaneous endoscopic/laparoscopic (`4`) approach (5th character),
- starts with one of the category's `surgery_pcs_prefixes` (e.g. `0D` GI tract, `00` nervous system), and
- isn't a bedside procedure listed in `surgery_exclusions.csv` (tracheostomy, chest tube, paracentesis, gastrostomy, ports).

This leaves out endoscopic hemostasis, endovascular coiling and TIPS. If you
have operating-room data, pass `surgery_stays={...}` and it replaces the
ICD-10-PCS definition.

### Trauma

`trauma_regions.csv` maps injury code prefixes to seven regions: head (incl.
neck), face, chest, abdomen, pelvis/hip, spinal (spine and cord) and extremity.
Superficial injuries (S00, S10, S20, …) and non-initial encounters are ignored.
For each stay:

- 1 region → `<region> only` (pelvis only = `Pelvis/hip trauma`; head only is its own group, `Head trauma`)
- 2 regions → the `<A>/<B> trauma` category for that pair
- 3+ regions → `<primary>/multiple trauma`. The primary region is the principal diagnosis's region; if the principal isn't an injury: head > spinal > chest > abdomen > pelvis > face > extremity.

In admission-diagnosis mode a trauma category is only assigned when the
principal diagnosis is itself an injury. Burns (T20–T32) and injuries with no
region (T07, T14, T79, frostbite) have their own categories.

### Poisoning intent and encounters

The intent of a T36–T65 code is the last non-`X` character before the 7th
character: usually the 6th (`T39.012A`), sometimes the 5th (`T58.01XA`).
1 accidental · 2 self-harm · 3 assault · 4 undetermined · 5 adverse effect · 6 underdosing.
Overdose categories take 1–4; `Toxicity, drug` also takes 5.

S and T codes end in A (initial), D (subsequent) or S (sequela). By default
only `A` codes match (`initial_encounter_only=True`).

### Known limits

- **Timing.** ICD codes don't record when something happened. "Cardiac arrest in the first 24 hours" and "MI > 24 h after onset" can't be read from codes alone; use present-on-admission indicators or pass stay ids via `external=`.
- **Flags count chronic disease.** Asthma, CHF, rheumatoid arthritis, anemia and similar flags turn on for any coded instance, including stable chronic disease. Narrow with present-on-admission or principal-diagnosis filters if you need acute episodes only.
- **Lithium** shares T43.59 with atypical antipsychotics, so it lands under sedatives/antipsychotics.
- **Spinal cord only trauma** also catches spine fractures without cord injury.
- **Weaning from ventilation** uses Z99.11, which can't tell a transfer-in for weaning from a long-term ventilated patient.
- **Older data.** Codes were checked against FY2026. Newer subcodes (e.g. I47.2x FY2024, K76.82 FY2023, F1x.13 FY2023) are matched by prefix, so older parent codes still match, but codes that only exist in older releases weren't checked.
- Category-specific caveats are in the `notes` column.

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
some PCS and CM codes look alike. `is_principal` is needed for
`assign_admission_dx`, and used by `flag_stays` to pick the primary trauma region.

```python
import pandas as pd
from apache_dx import flag_stays, assign_admission_dx

codes = pd.read_csv("examples/example_codes.csv")

# 1) Yes/no flags using every code on the stay
flags = flag_stays(codes)                      # one column per group
detail = flag_stays(codes, level="category")   # one column per APACHE category

# 2) One APACHE admission diagnosis per stay
#    (principal diagnosis + procedures, lowest priority wins)
adm = assign_admission_dx(codes)

# Options
flag_stays(codes, include_optional=False)       # drop borderline codes
flag_stays(codes, include_catch_all=True)       # add the "medical, other" catch-alls
flag_stays(codes, initial_encounter_only=False) # keep D/S encounters
flag_stays(codes, surgery_stays={5, 12})        # your own definition of "had surgery"
flag_stays(codes, external={
    "MI admitted > 24 hrs after onset of ischemia": {4, 17, 23},  # stay ids from your timing data
})
```

Column names can be changed with `stay_col=`, `code_col=`, `system_col=` and
`principal_col=`. A 5,000-stay, 60,000-code table runs in a few seconds.

Output of `flag_stays` on the example file (only groups with a hit shown):

```
group    Cardiovascular  Angina, stable  Aortic aneurysm   ARDS  Toxicology    CHF
stay_id
1                 False           False            False  False        True  False
2                 False           False            False  False        True  False
3                 False           False            False  False        True  False
4                  True            True            False   True       False  False
5                 False           False             True  False       False  False
6                  True           False            False  False       False  False
7                 False           False            False  False       False   True
8                 False           False            False  False        True  False
```

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
- ICD-10-CM code list used for validation: CMS/CDC April 2026 release, via https://github.com/StefanoTrv/simple_icd_10_CM

This repository covers APACHE **admission diagnosis categories**. It does not
cover the APACHE II chronic-health comorbidity points; see Kao & Gulbis for those.
