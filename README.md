# APACHE II diagnosis ↔ ICD-10 crosswalk

CSV mappings from **ICD-10-CM diagnosis** and **ICD-10-PCS procedure** codes to
APACHE admission-diagnosis categories: 231 categories across 43 groups
(cardiovascular, respiratory, neurologic, GI, genitourinary, infection,
metabolic, hematologic, cancer, musculoskeletal, toxicology and trauma).

Each **group** is one yes/no flag ("diagnosed with a GI bleed during the ICU
stay"). Each **category** is one APACHE admission-diagnosis option, worded as
on the APACHE form.

> **Status: not clinically validated.** The lists were built from ICD-10-CM/PCS
> code structure and APACHE category wording. They are not an official or
> published APACHE crosswalk. Every ICD-10-CM code and prefix has been checked
> to exist in the April 2026 ICD-10-CM release, but whether a code *belongs* in
> a category is a judgement call. Review the `notes` column and a sample of
> charts before using these in analyses.

## Files

| File | Contents |
|---|---|
| `data/categories.csv` | One row per APACHE category: group, matching rule, extra conditions, notes |
| `data/code_map.csv` | One row per ICD code, prefix or range mapped to a category |
| `data/trauma_regions.csv` | ICD-10-CM injury prefixes → body region (head, face, chest, abdomen, pelvis, spinal, extremity) |
| `data/surgery_exclusions.csv` | Bedside ICD-10-PCS procedures that don't count as "surgery" |

## Groups

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

## `data/code_map.csv`

| Column | Meaning |
|---|---|
| `group`, `category` | Same as `categories.csv` |
| `code` | ICD code as usually written (with dot), or a range such as `T36-T50` |
| `code_system` | `ICD-10-CM`, `ICD-10-PCS`, or `ICD-10 (WHO)` (X60–X69, which don't exist in ICD-10-CM) |
| `match_type` | `Exact` = that code only · `Prefix` = the code and all its children (`I33` matches `I33.0`, `I33.9`) · `Range` = any code whose 3-character category falls in the range |
| `intent_chars` | For poisoning codes T36–T65: allowed intent characters (see below). Blank = any |
| `last_char_in` | Allowed final characters. Used to separate stable angina (`1, 2, 8, 9`) from unstable (`0`) in the I25.11x / I25.7xx families |
| `optional` | `TRUE` for borderline codes you may want to drop |
| `exclude` | `TRUE` = this row **removes** the category for codes it matches. E.g. Anemia is `D50-D64` minus `D61.81` (pancytopenia) and the sickle-crisis codes |
| `description` | Short code description |

Compare codes with the dots removed (`T39.012A` → `T39012A`) if your data stores them that way.

## `data/categories.csv`

| Column | Meaning |
|---|---|
| `group` | The flag the category rolls up to |
| `category` | APACHE admission diagnosis |
| `rule` | How the category is matched (table below) |
| `required_dx_prefixes` | For `with_required_dx`: the stay must also have a diagnosis starting with one of these (pipe-separated) |
| `exclude_dx_prefixes` | Drop the category if the stay has any diagnosis starting with one of these |
| `requires_surgery` | `TRUE` for "surgery for" categories (see Surgery) |
| `surgery_pcs_prefixes` | ICD-10-PCS prefixes the operation must start with (blank = any operation) |
| `trauma_pattern` | For `rule = trauma`: body regions, e.g. `head+extremity`, `chest+multiple`, `face` |
| `catch_all` | `TRUE` for the broad "<system> medical, other" categories |
| `notes` | Coding caveats and judgement calls |

| `rule` | Category is met when… |
|---|---|
| `any` | any of its codes in `code_map.csv` is present |
| `residual` | one of its codes is present **and no non-residual category claims that same code**. E.g. `Leukemia, other` = a C91–C95 code not claimed by ALL/AML/CLL/CML; `GI medical, other` = a K20–K95 code no other category claims |
| `dx_and_procedure` | one of its ICD-10-CM codes **and** one of its ICD-10-PCS codes (VSD repair, ventricular aneurysm repair) |
| `with_required_dx` | one of its codes **and** a diagnosis from `required_dx_prefixes` on the same stay (sepsis by site, aortic aneurysm repair, open-heart complications) |
| `needs_external_data` | codes can't decide it (MI admitted > 24 h after ischemia onset needs symptom-onset time) |
| `trauma` | no code rows; the stay's injured body regions equal `trauma_pattern` (see Trauma) |
| `surgery_only` | no code rows; the stay had an operation starting with `surgery_pcs_prefixes` (hematologic surgery) |

`exclude_dx_prefixes` and `requires_surgery` apply on top of the rule.

## Matching conventions

### Flags vs. one admission diagnosis

- **Yes/no flag per group:** match against every diagnosis and procedure code on the stay.
- **One admission diagnosis per stay:** match the principal diagnosis (plus procedures, plus any diagnosis for `required_dx_prefixes` / `exclude_dx_prefixes`). Some codes match more than one category (e.g. a bleeding ulcer matches both `Bleeding, upper GI` and `Ulcer disease, peptic`; the `notes` column calls out these overlaps), so you'll need your own rule for picking one.

### Catch-all categories

The "medical, other" categories (`catch_all = TRUE`) take every leftover code in
their ICD chapter. That's what APACHE intends for a *principal* diagnosis, but
as yes/no flags they'd turn on for almost anyone: reflux, hyperlipidemia, COPD,
acute kidney injury. Leave them out when building flags; keep them when
assigning an admission diagnosis.

Other residual categories (`Leukemia, other`, `Overdose, other toxin, poison or drug`, `Cancer, other site`) are specific enough to keep in both.

### Sepsis by site

ICD-10-CM codes sepsis (A40/A41, R65.2, …) separately from the site of
infection. Each "Sepsis, <site>" category needs **a sepsis code plus a
site-of-infection code** (`required_dx_prefixes`) on the same stay: pneumonia →
pulmonary, UTI or pyelonephritis → renal/UTI, peritonitis or C. difficile → GI,
and so on. `Sepsis, unknown` = a sepsis code with **no** site code
(`exclude_dx_prefixes`). A stay with several sites matches several sepsis
categories.

### Surgery ("surgery for" categories)

A stay counts as having surgery when it has an ICD-10-PCS code that:

- is in the Medical and Surgical section (first character `0`),
- uses an open (`0`) or percutaneous endoscopic/laparoscopic (`4`) approach (5th character),
- starts with one of the category's `surgery_pcs_prefixes` (e.g. `0D` GI tract, `00` nervous system; blank = any), and
- doesn't start with a prefix in `surgery_exclusions.csv` (tracheostomy, chest tube, paracentesis, gastrostomy, ports).

This leaves out endoscopic hemostasis, endovascular coiling and TIPS. If you
have operating-room data, it's a better source for "had surgery".

### Trauma

`trauma_regions.csv` maps injury code prefixes to seven regions: head (incl.
neck), face, chest, abdomen, pelvis/hip, spinal (spine and cord) and extremity.
When prefixes overlap, the longest one wins (`S22` → chest, `S22.0` → spinal).
Superficial injuries (S00, S10, S20, …) aren't mapped, and only initial-encounter
codes (7th character `A`) count. For each stay:

- 1 region → `<region> only` (pelvis only = `Pelvis/hip trauma`; head only is its own group, `Head trauma`)
- 2 regions → the `<A>/<B> trauma` category for that pair
- 3+ regions → `<primary>/multiple trauma`. The primary region is the principal diagnosis's region; if the principal isn't an injury, take the first present of head > spinal > chest > abdomen > pelvis > face > extremity.

When assigning one admission diagnosis, only use a trauma category if the
principal diagnosis is itself an injury. Burns (T20–T32) and injuries with no
region (T07, T14, T79, frostbite) have their own categories.

### Poisoning intent and encounters

The intent of a T36–T65 code is the last non-`X` character before the 7th
character: usually the 6th (`T39.012A`), sometimes the 5th (`T58.01XA`).

| Intent | Meaning | Used by |
|---|---|---|
| 1 | accidental | overdose categories |
| 2 | intentional self-harm | overdose categories, `Overdose, self-inflicted` |
| 3 | assault | overdose categories |
| 4 | undetermined | overdose categories |
| 5 | adverse effect (therapeutic use) | `Toxicity, drug` only |
| 6 | underdosing | none |

S and T codes end in A (initial), D (subsequent) or S (sequela). For ICU
admissions, use only `A` codes.

### Known limits

- **Timing.** ICD codes don't record when something happened. "Cardiac arrest in the first 24 hours" and "MI > 24 h after onset" can't be read from codes alone; use present-on-admission indicators or chart data.
- **Flags count chronic disease.** Asthma, CHF, rheumatoid arthritis, anemia and similar flags turn on for any coded instance, including stable chronic disease. Narrow with present-on-admission or principal-diagnosis filters if you need acute episodes only.
- **Lithium** shares T43.59 with atypical antipsychotics, so it lands under sedatives/antipsychotics.
- **Spinal cord only trauma** also catches spine fractures without cord injury.
- **Weaning from ventilation** uses Z99.11, which can't tell a transfer-in for weaning from a long-term ventilated patient.
- **Older data.** Codes were checked against FY2026. Newer subcodes (e.g. I47.2x FY2024, K76.82 FY2023, F1x.13 FY2023) are listed as prefixes, so older parent codes still match, but codes that only exist in older releases weren't checked.
- Category-specific caveats are in the `notes` column.

## Background and related work

- Knaus WA, Draper EA, Wagner DP, Zimmerman JE. APACHE II: a severity of disease classification system. *Crit Care Med.* 1985;13(10):818–829.
- Kao E, Gulbis B. *Validation of ICD-9-CM/ICD-10-CM Codes for Automated Electronic Scoring of APACHE II, APACHE III, and SAPS II* (supplemental material, 2016). ICD lists for the APACHE II chronic-health comorbidities: https://rstudio-pubs-static.s3.amazonaws.com/231351_940f14aa51a6427a9e92d5a04daefc3e.html
- `icuriskr` (R): https://github.com/bgulbis/icuriskr
- MIT-LCP `mimic-code` (SQL for SAPS II / APS III, including surgical-status flags): https://github.com/MIT-LCP/mimic-code
- ICD-10-CM code list used for validation: CMS/CDC April 2026 release, via https://github.com/StefanoTrv/simple_icd_10_CM

These mappings cover APACHE **admission diagnosis categories**. They don't
cover the APACHE II chronic-health comorbidity points; see Kao & Gulbis for those.
