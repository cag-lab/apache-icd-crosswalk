import pandas as pd
import pytest

from apache_ii import flag_stays, principal_categories
from apache_ii.match import intent_char, normalize

CM, PCS = "ICD-10-CM", "ICD-10-PCS"


def codes(*rows):
    return pd.DataFrame(rows, columns=["stay_id", "code", "code_system", "is_principal"])


def cats(df, **kw):
    f = flag_stays(df, level="category", **kw)
    return {s: set(f.columns[f.loc[s]]) for s in f.index}


@pytest.mark.parametrize("code,expected", [
    ("T39012A", "2"), ("T510X1A", "1"), ("T5801XA", "1"), ("T5802XA", "2"),
    ("T461X5A", "5"), ("T40411A", "1"),
])
def test_intent_char(code, expected):
    assert intent_char(code) == expected


def test_normalize():
    assert normalize(" t39.012a ") == "T39012A"


def test_intentional_aspirin_overdose():
    c = cats(codes((1, "T39.012A", CM, True)))[1]
    assert c == {"Overdose, analgesic (aspirin, acetaminophen)", "Overdose, self-inflicted"}


def test_carbon_monoxide_intent_in_fifth_position():
    c = cats(codes((1, "T58.01XA", CM, True), (2, "T58.02XA", CM, True)))
    assert c[1] == {"Poisoning, carbon monoxide, arsenic, cyanide"}
    assert "Overdose, self-inflicted" in c[2]
    assert "Overdose, other toxin, poison or drug" not in c[2]  # residual suppressed


def test_adverse_effect_is_toxicity_not_overdose():
    c = cats(codes((1, "T46.1X5A", CM, True)))[1]
    assert c == {"Toxicity, drug (i.e., beta blockers, calcium channel blockers, etc.)"}


def test_subsequent_encounter_excluded():
    c = cats(codes((1, "T39.011D", CM, True)))[1]
    assert c == set()
    c = cats(codes((1, "T39.011D", CM, True)), initial_encounter_only=False)[1]
    assert c == {"Overdose, analgesic (aspirin, acetaminophen)"}


def test_unstable_vs_stable_angina():
    c = cats(codes((1, "I25.110", CM, True), (2, "I25.701", CM, True)))
    assert c[1] == {"Angina, unstable"}
    assert c[2] == {"Angina, stable (asymp or stable pattern of symptoms w/meds)"}


def test_aortic_aneurysm_not_in_cardiovascular_residual():
    f = flag_stays(codes((1, "I71.4", CM, True), (2, "I87.2", CM, True)), include_catch_all=True)
    assert f.loc[1, "Aortic aneurysm"] and not f.loc[1, "Cardiovascular"]
    assert f.loc[2, "Cardiovascular"] and not f.loc[2, "Aortic aneurysm"]


def test_catch_all_off_by_default_in_flags():
    df = codes((1, "K59.00", CM, True))
    assert not flag_stays(df).loc[1].any()
    assert flag_stays(df, include_catch_all=True).loc[1, "GI"]


def test_open_heart_complication_needs_status_code():
    c = "Complications of previous open heart surgery"
    assert c not in cats(codes((1, "T81.41XA", CM, True)))[1]
    assert c in cats(codes((1, "T81.41XA", CM, True), (1, "Z95.1", CM, False)))[1]


def test_sepsis_site_rules():
    c = cats(codes(
        (1, "A41.9", CM, True), (1, "J18.9", CM, False),
        (2, "A41.9", CM, True),
        (3, "A41.51", CM, True), (3, "N39.0", CM, False),
    ))
    assert {"Sepsis, pulmonary", "Pneumonia, other"} <= c[1] and "Sepsis, unknown" not in c[1]
    assert c[2] == {"Sepsis, unknown"}
    assert "Sepsis, renal/UTI (including bladder)" in c[3] and "Sepsis, pulmonary" not in c[3]




def test_coma_excludes_cardiac_arrest():
    assert "Coma/change in level of consciousness" in cats(codes((1, "R40.20", CM, True)))[1]
    assert "Coma/change in level of consciousness" not in cats(codes((1, "R40.20", CM, True), (1, "I46.9", CM, False)))[1]


def test_respiratory_arrest_excludes_cardiac_arrest():
    f = flag_stays(codes((1, "R09.2", CM, True), (2, "R09.2", CM, True), (2, "I46.9", CM, False)))
    assert f.loc[1, "Respiratory arrest"] and not f.loc[2, "Respiratory arrest"]


def test_leukemia_residual_and_cancer_other():
    c = cats(codes((1, "C92.00", CM, True), (2, "C91.40", CM, True), (3, "C50.911", CM, True)))
    assert c[1] == {"Leukemia, acute myelocytic"}
    assert c[2] == {"Leukemia, other"}
    assert c[3] == {"Cancer, other site (not an APACHE option; counts toward the flag)"}


def test_exclude_rows():
    c = cats(codes((1, "D61.818", CM, True), (2, "D57.00", CM, True), (3, "D62", CM, True)))
    assert c[1] == {"Pancytopenia"}
    assert c[2] == {"Sickle cell crisis"}
    assert c[3] == {"Anemia"}


def test_surgery_variants():
    perf = "GI perforation/rupture, surgery for"
    assert perf not in cats(codes((1, "K63.1", CM, True)))[1]
    assert perf in cats(codes((1, "K63.1", CM, True), (1, "0DTN0ZZ", PCS, False)))[1]
    # percutaneous procedure doesn't count as surgery
    assert perf not in cats(codes((1, "K63.1", CM, True), (1, "0DTN3ZZ", PCS, False)))[1]
    # tracheostomy is excluded even though it's open
    assert "Pancreatitis, surgery for" not in cats(codes((1, "K85.90", CM, True), (1, "0B110F4", PCS, False)))[1]
    # surgery_stays overrides the PCS definition
    assert perf in cats(codes((1, "K63.1", CM, True)), surgery_stays={1})[1]


def test_hematologic_surgery_only():
    f = flag_stays(codes((1, "07TP0ZZ", PCS, False), (1, "D73.5", CM, True), (2, "07TP3ZZ", PCS, False), (2, "D73.5", CM, True)))
    assert f.loc[1, "Hematologic surgery"] and not f.loc[2, "Hematologic surgery"]


def test_trauma_regions():
    c = cats(codes(
        (1, "S06.0X0A", CM, True),                                   # head only
        (2, "S06.0X0A", CM, True), (2, "S82.101A", CM, False),       # head + extremity
        (3, "S72.001A", CM, True),                                   # hip -> pelvis
        (4, "S27.0XXA", CM, True), (4, "S36.116A", CM, False), (4, "S82.101A", CM, False),  # 3 regions, chest principal
        (5, "S02.401A", CM, True), (5, "S82.101A", CM, False), (5, "0QSG04Z", PCS, False),  # face + extremity + surgery
        (6, "S00.03XA", CM, True),                                   # superficial only -> no region
        (7, "S06.0X0D", CM, True),                                   # subsequent encounter -> ignored
    ))
    assert "Head only trauma" in c[1]
    assert "Head/extremity trauma" in c[2]
    assert "Pelvis/hip trauma" in c[3]
    assert "Chest/multiple trauma" in c[4]
    assert {"Extremity/face trauma", "Extremity/face trauma, surgery for"} <= c[5]
    assert c[6] == set() and c[7] == set()






def test_seizure_excludes_structural():
    f = flag_stays(codes((1, "G40.909", CM, True), (2, "G40.909", CM, True), (2, "C71.9", CM, False)))
    assert f.loc[1, "Seizures"] and not f.loc[2, "Seizures"]


def test_smoke_inhalation_intent():
    assert "Smoke inhalation" in cats(codes((1, "T59.811A", CM, True)))[1]


def test_every_category_has_a_way_to_match():
    from apache_ii.match import load_categories, load_code_map
    cats_df, cmap = load_categories(), load_code_map()
    structural = cats_df["rule"].isin(["trauma", "surgery_only"])
    coded = set(cmap["category"])
    missing = cats_df.loc[~structural & ~cats_df["category"].isin(coded), "category"].tolist()
    assert missing == []


def test_vsd_repair_needs_dx_and_procedure():
    dx_only = cats(codes((1, "Q21.0", CM, True)))[1]
    both = cats(codes((1, "Q21.0", CM, True), (1, "02QM0ZZ", PCS, False)))[1]
    assert "Ventricular Septal Defect (VSD) Repair" not in dx_only
    assert "Ventricular Septal Defect (VSD) Repair" in both


def test_aortic_repair_needs_aneurysm_dx():
    rep = "Aneurysms, repair of other (except ventricular)"
    assert rep in cats(codes((1, "I71.4", CM, True), (1, "04R00JZ", PCS, False)))[1]
    assert rep not in cats(codes((1, "I10", CM, True), (1, "04V00DZ", PCS, False)))[1]


def test_mi_over_24h_needs_external_data():
    late = "MI admitted > 24 hrs after onset of ischemia"
    df = codes((1, "I21.4", CM, True))
    assert late not in cats(df)[1]
    assert late in cats(df, external={late: {1}})[1]


def test_optional_codes_can_be_dropped():
    df = codes((1, "I25.10", CM, True))
    assert flag_stays(df).loc[1, "Angina, stable"]
    assert not flag_stays(df, include_optional=False).loc[1, "Angina, stable"]






def test_principal_categories_use_principal_only():
    df = codes((1, "I21.4", CM, True), (1, "J80", CM, False), (2, "A41.9", CM, True), (2, "J18.9", CM, False))
    pc = principal_categories(df)
    c1 = set(pc.loc[pc.stay_id == 1, "category"])
    c2 = set(pc.loc[pc.stay_id == 2, "category"])
    assert "Infarction, acute myocardial (MI)" in c1 and not any("ARDS" in c for c in c1)
    assert "Sepsis, pulmonary" in c2
