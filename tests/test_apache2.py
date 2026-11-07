import math

import pandas as pd
import pytest

from apache_ii import age_points, apache2, apache2_total, chronic_health_points, load_weights, predicted_mortality

CM, PCS = "ICD-10-CM", "ICD-10-PCS"


def codes(*rows):
    return pd.DataFrame(rows, columns=["stay_id", "code", "code_system", "is_principal"])


def stays(*rows, cols=("stay_id", "aps", "age", "operative_status")):
    return pd.DataFrame(rows, columns=list(cols))


def by_stay(df):
    return df.set_index("stay_id")


def mort(score, weight, emergency=False):
    logit = -3.517 + 0.146 * score + weight + (0.603 if emergency else 0)
    return 1 / (1 + math.exp(-logit))


# ----------------------------------------------------------------- scalar pieces
@pytest.mark.parametrize("age,pts", [(18, 0), (44, 0), (45, 2), (54, 2), (55, 3), (64, 3), (65, 5), (74, 5), (75, 6), (99, 6)])
def test_age_points(age, pts):
    assert age_points(age) == pts


def test_chronic_health_points():
    assert chronic_health_points(True, "nonoperative") == 5
    assert chronic_health_points(True, "emergency") == 5
    assert chronic_health_points(True, "elective") == 2
    assert chronic_health_points(False, "emergency") == 0


def test_total_and_mortality():
    assert apache2_total(20, 70, True, "nonoperative") == 30
    assert predicted_mortality(30, 0.113, "nonoperative") == pytest.approx(mort(30, 0.113))
    assert predicted_mortality(20, -0.955, "emergency") == pytest.approx(mort(20, -0.955, True))


def test_weights_table_matches_knaus():
    w = load_weights().set_index(["setting", "knaus_category"])["weight"]
    assert w[("nonoperative", "Drug overdose")] == -3.353
    assert w[("nonoperative", "Respiratory")] == -0.890
    assert w[("postoperative", "Neurologic")] == -1.150
    assert w[("postoperative", "GI perforation/obstruction")] == 0.060
    assert len(w) == 29 + 21


# ----------------------------------------------------------------- end to end
def test_sepsis_with_esrd():
    r = by_stay(apache2(stays((1, 20, 70, "nonoperative")),
                        codes((1, "A41.9", CM, True), (1, "J18.9", CM, False), (1, "N18.6", CM, False))))
    assert r.at[1, "chronic_health_conditions"] == "Renal"
    assert r.at[1, "apache2_score"] == 30
    assert r.at[1, "knaus_category"] == "Sepsis"
    assert r.at[1, "predicted_mortality"] == pytest.approx(mort(30, 0.113))


def test_copd_code_override():
    r = by_stay(apache2(stays((1, 15, 66, "nonoperative")), codes((1, "J44.1", CM, True))))
    assert r.at[1, "knaus_category"] == "COPD" and r.at[1, "dx_weight"] == -0.367


def test_postop_sepsis_uses_nonoperative_weight():
    r = by_stay(apache2(stays((1, 20, 60, "emergency")),
                        codes((1, "A41.9", CM, True), (1, "K65.0", CM, False), (1, "0DTN0ZZ", PCS, False))))
    assert (r.at[1, "knaus_setting"], r.at[1, "knaus_category"]) == ("nonoperative", "Sepsis")
    assert r.at[1, "predicted_mortality"] == pytest.approx(mort(20 + 3, 0.113, True))


def test_craniotomy_for_sdh_beats_head_trauma():
    r = by_stay(apache2(stays((1, 18, 50, "emergency")), codes((1, "S06.5X0A", CM, True), (1, "00C40ZZ", PCS, False))))
    assert r.at[1, "knaus_category"] == "Craniotomy for ICH/SDH/SAH"
    assert r.at[1, "predicted_mortality"] == pytest.approx(mort(20, -0.788, True))


def test_postop_procedure_rules():
    df = codes(
        (1, "I35.0", CM, True), (1, "02RF0JZ", PCS, False),          # valve
        (2, "N18.6", CM, True), (2, "0TY00Z0", PCS, False),          # renal transplant
        (3, "C34.11", CM, True), (3, "0BTC0ZZ", PCS, False),         # lobectomy for cancer
        (4, "C18.7", CM, True), (4, "0DTN0ZZ", PCS, False),          # colectomy for cancer
        (5, "S82.101A", CM, True), (5, "0QSG04Z", PCS, False),       # trauma with fixation
        (6, "I25.10", CM, True), (6, "02100Z9", PCS, False),         # CABG
    )
    s = stays(*[(i, 10, 60, "elective") for i in range(1, 7)])
    r = by_stay(apache2(s, df))["knaus_category"]
    assert r[1] == "Heart valve surgery"
    assert r[2] == "Renal transplant"
    assert r[3] == "Thoracic surgery for neoplasm"
    assert r[4] == "GI surgery for neoplasm"
    assert r[5] == "Multiple trauma"
    assert r[6] == "Admission due to chronic cardiovascular disease"


def test_elective_chronic_health_two_points():
    r = by_stay(apache2(stays((1, 10, 40, "elective")),
                        codes((1, "C18.7", CM, True), (1, "Z79.52", CM, False), (1, "0DTN0ZZ", PCS, False))))
    assert r.at[1, "chronic_health_points"] == 2 and r.at[1, "apache2_score"] == 12


def test_overrides():
    s = pd.DataFrame([(1, 10, 40, "nonoperative", False, "Hypertension")],
                     columns=["stay_id", "aps", "age", "operative_status", "chronic_health", "knaus_category"])
    r = by_stay(apache2(s, codes((1, "A41.9", CM, True), (1, "N18.6", CM, False))))
    assert not r.at[1, "chronic_health"] and r.at[1, "chronic_health_source"] == "input"
    assert r.at[1, "knaus_category"] == "Hypertension" and r.at[1, "knaus_source"] == "input"
    bad = s.assign(knaus_category="Not a category")
    with pytest.raises(ValueError):
        apache2(bad, codes((1, "A41.9", CM, True)))


def test_unmapped_and_chapter_fallback():
    df = codes((1, "L03.115", CM, True), (2, "I87.2", CM, True), (3, "I67.89", CM, True))
    s = stays((1, 10, 40, "nonoperative"), (2, 10, 40, "nonoperative"), (3, 10, 40, "nonoperative"))
    r = by_stay(apache2(s, df))
    assert pd.isna(r.at[1, "predicted_mortality"])
    assert r.at[2, "knaus_category"] == "Cardiovascular"
    assert r.at[3, "knaus_category"] == "Neurologic"
    r = by_stay(apache2(s, df, unmapped_system="Metabolic/renal"))
    assert r.at[1, "knaus_category"] == "Metabolic/renal"


def test_chronic_health_definitions():
    df = codes(
        (1, "K70.30", CM, True),                              # cirrhosis alone: no
        (2, "K70.30", CM, True), (2, "K76.6", CM, False),     # cirrhosis + portal HTN: yes
        (3, "I50.9", CM, True),                               # any HF: optional only
        (4, "I50.84", CM, True),                              # end-stage HF: yes
        (5, "Z94.0", CM, True),                               # kidney transplant: immunocompromised
    )
    s = stays(*[(i, 10, 40, "nonoperative") for i in range(1, 6)])
    r = by_stay(apache2(s, df))["chronic_health"]
    assert list(r) == [False, True, False, True, True]
    r = by_stay(apache2(s, df, include_optional_chronic=True))["chronic_health"]
    assert r[3]


def test_acute_renal_failure_flag():
    df = codes((1, "N17.9", CM, True), (2, "N17.9", CM, True), (2, "N18.6", CM, False))
    r = by_stay(apache2(stays((1, 10, 40, "nonoperative"), (2, 10, 40, "nonoperative")), df))
    assert r.at[1, "acute_renal_failure_coded"] and not r.at[2, "acute_renal_failure_coded"]


def test_bad_status():
    with pytest.raises(ValueError):
        apache2(stays((1, 10, 40, "maybe")), codes((1, "A41.9", CM, True)))


def test_every_dx_map_category_exists():
    import csv
    from pathlib import Path
    d = Path(__file__).parent.parent / "data"
    cats = {r["category"] for r in csv.DictReader(open(d / "categories.csv"))}
    mapped = {r["category"] for r in csv.DictReader(open(d / "knaus_dx_map.csv"))}
    assert cats == mapped


def test_covid_respiratory_failure():
    df = codes(
        (1, "U07.1", CM, True), (1, "J12.82", CM, False), (1, "J96.01", CM, False),   # COVID pneumonia + resp failure
        (2, "A41.89", CM, True), (2, "U07.1", CM, False),                             # viral sepsis due to COVID
        (3, "U07.0", CM, True),                                                       # vaping injury, not COVID
    )
    s = stays((1, 20, 60, "nonoperative"), (2, 20, 60, "nonoperative"), (3, 20, 60, "nonoperative"))
    r = by_stay(apache2(s, df))
    assert r.at[1, "knaus_category"] == "Infection" and r.at[1, "dx_weight"] == 0.0
    assert r.at[1, "predicted_mortality"] == pytest.approx(mort(23, 0.0))
    assert r.at[2, "knaus_category"] == "Sepsis"
    assert r.at[3, "knaus_category"] == "Aspiration/poisoning/toxic"


def test_covid_flags():
    from apache_ii import flag_stays
    f = flag_stays(codes((1, "U07.1", CM, True), (2, "A41.89", CM, True), (2, "U07.1", CM, False)), level="category")
    assert f.loc[1, "COVID-19 (not an APACHE option; counts as respiratory infection)"]
    assert f.loc[2, "Sepsis, pulmonary"] and not f.loc[2, "Sepsis, unknown"]


# ------------------------------------------------- organ failure, shock, APS range
def test_respiratory_failure_principal_uses_cause():
    # Knaus weights respiratory failure by its cause; J96.x coded first
    # shouldn't collapse every cause into the generic "Respiratory" weight.
    df = codes(
        (1, "J96.01", CM, True), (1, "J18.9", CM, False),                         # from pneumonia
        (2, "J96.02", CM, True), (2, "J44.1", CM, False),                         # from COPD
        (3, "J96.01", CM, True), (3, "J44.9", CM, False), (3, "J18.9", CM, False),  # COPD + pneumonia -> infection
        (4, "J96.01", CM, True), (4, "I26.99", CM, False),                        # from PE
        (5, "J96.01", CM, True), (5, "C34.90", CM, False),                        # from lung cancer
        (6, "J96.01", CM, True),                                                  # no cause coded
    )
    s = stays(*[(i, 20, 60, "nonoperative") for i in range(1, 7)])
    r = by_stay(apache2(s, df))
    assert r.at[1, "knaus_category"] == "Infection" and r.at[1, "dx_weight"] == 0.0
    assert r.at[2, "knaus_category"] == "COPD"
    assert r.at[3, "knaus_category"] == "Infection"
    assert r.at[4, "knaus_category"] == "Pulmonary embolus"
    assert r.at[5, "knaus_category"] == "Neoplasm"
    assert r.at[6, "knaus_category"] == "Respiratory"
    assert r.at[1, "predicted_mortality"] == pytest.approx(mort(23, 0.0))


def test_respiratory_failure_postop_unchanged():
    df = codes((1, "J96.01", CM, True), (1, "J18.9", CM, False))
    r = by_stay(apache2(stays((1, 20, 60, "elective")), df))
    assert r.at[1, "knaus_category"] == "Respiratory insufficiency after surgery"


def test_shock_principal_diagnoses():
    df = codes(
        (1, "R57.1", CM, True),                                   # hypovolemic shock
        (2, "R57.9", CM, True), (2, "A41.9", CM, False),          # shock + sepsis
        (3, "R57.9", CM, True), (3, "K92.2", CM, False),          # shock + GI bleed
        (4, "R57.1", CM, True),                                   # post-op
        (5, "R57.9", CM, True),                                   # no cause coded
    )
    s = stays((1, 20, 60, "nonoperative"), (2, 20, 60, "nonoperative"), (3, 20, 60, "nonoperative"),
              (4, 20, 60, "emergency"), (5, 20, 60, "nonoperative"))
    r = by_stay(apache2(s, df))
    assert r.at[1, "knaus_category"] == "Hemorrhagic shock/hypovolemia" and r.at[1, "dx_weight"] == 0.493
    assert r.at[2, "knaus_category"] == "Sepsis"
    assert r.at[3, "knaus_category"] == "GI bleeding"
    assert r.at[4, "knaus_category"] == "Hemorrhagic shock" and r.at[4, "dx_weight"] == -0.682
    assert r.at[5, "knaus_category"] == "Cardiovascular"


@pytest.mark.parametrize("aps", [-1, 61])
def test_aps_out_of_range(aps):
    with pytest.raises(ValueError, match="outside 0-60"):
        apache2(stays((1, aps, 60, "nonoperative")), codes((1, "I50.9", CM, True)))


def test_aps_bounds_accepted():
    r = apache2(stays((1, 0, 60, "nonoperative"), (2, 60, 60, "nonoperative")),
                codes((1, "I50.9", CM, True), (2, "I50.9", CM, True)))
    assert list(r["apache2_score"]) == [3.0, 63.0]
