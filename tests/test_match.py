import pandas as pd
import pytest

from apache_dx import assign_admission_dx, flag_stays, normalize
from apache_dx.match import intent_char

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
    f = flag_stays(codes((1, "I71.4", CM, True), (2, "I50.9", CM, True)))
    assert f.loc[1, "Aortic aneurysm"] and not f.loc[1, "Cardiovascular"]
    assert f.loc[2, "Cardiovascular"] and not f.loc[2, "Aortic aneurysm"]


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


def test_assign_admission_dx_uses_principal_and_priority():
    df = codes(
        (1, "I21.4", CM, True), (1, "J80", CM, False),
        (2, "I71.4", CM, True), (2, "04R00JZ", PCS, False),
        (3, "Z99.89", CM, True),
    )
    a = assign_admission_dx(df).set_index("stay_id")["category"]
    assert a[1] == "Infarction, acute myocardial (MI)"  # J80 is secondary
    assert a[2] == "Aneurysm, abdominal aortic"  # priority beats the repair row
    assert pd.isna(a[3])


def test_example_file_runs():
    from pathlib import Path
    df = pd.read_csv(Path(__file__).parent.parent / "examples" / "example_codes.csv")
    f = flag_stays(df)
    assert f.shape[0] == df["stay_id"].nunique()
