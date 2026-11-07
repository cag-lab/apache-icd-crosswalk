"""scripts/check_codes.py on a tiny synthetic release (no CMS files needed)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_codes", ROOT / "scripts" / "check_codes.py")
check_codes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_codes)


def order_file(tmp_path, entries):
    """entries: (code, billable) -> fixed-width CMS order-file lines."""
    lines = [f"{i:05d} {code:<7} {int(b)} {'short':<60} long" for i, (code, b) in enumerate(entries, 1)]
    p = tmp_path / "order.txt"
    p.write_text("\n".join(lines) + "\n", encoding="latin-1")
    return p


def test_load_order_reads_billable_flag(tmp_path):
    every, billable = check_codes.load_order(order_file(tmp_path, [("A00", 0), ("A000", 1), ("A001", 1)]))
    assert every == {"A00", "A000", "A001"}
    assert billable == ["A000", "A001"]


def test_flags_header_used_as_exact_and_dead_prefix(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    header = "group,category,code,code_system,match_type,intent_chars,last_char_in,optional,exclude,description\n"
    (data / "code_map.csv").write_text(header +
        "G,C,A00,ICD-10-CM,Exact,,,FALSE,FALSE,header not billable\n"
        "G,C,B99,ICD-10-CM,Prefix,,,FALSE,FALSE,no such code\n"
        "G,C,A000,ICD-10-CM,Exact,,,FALSE,FALSE,fine\n")
    (data / "chronic_health_codes.csv").write_text(header)
    cat_header = "group,category,rule,required_dx_prefixes,exclude_dx_prefixes,requires_surgery,surgery_pcs_prefixes,trauma_pattern,catch_all,notes\n"
    for name in ("categories.csv", "chronic_health_categories.csv"):
        (data / name).write_text(cat_header)
    (data / "trauma_regions.csv").write_text("prefix,region\n")
    (data / "knaus_dx_map.csv").write_text("category,code_prefixes,nonoperative_knaus,postoperative_knaus,basis,notes\n")
    (data / "knaus_postop_procedures.csv").write_text("knaus_category,pcs_prefixes,required_dx_prefixes,notes\n")
    monkeypatch.setattr(check_codes, "DATA", data)
    cm = check_codes.Release(order_file(tmp_path, [("A00", 0), ("A000", 1)]))
    errors = check_codes.check(cm, None)
    assert len(errors) == 2
    assert any("A00 is a header" in e for e in errors)
    assert any("B99 matches no billable code" in e for e in errors)


def test_repo_data_parses():
    # every data file the checker reads must parse with the expected columns
    for name in ("code_map.csv", "chronic_health_codes.csv", "categories.csv", "chronic_health_categories.csv",
                 "trauma_regions.csv", "knaus_dx_map.csv", "knaus_postop_procedures.csv", "surgery_exclusions.csv"):
        assert check_codes.rows(name), name
