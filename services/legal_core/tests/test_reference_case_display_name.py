from types import SimpleNamespace

from legal_core.reference_evaluation_api import _display_name


def test_imported_case_has_a_compact_bounded_title_without_exposing_its_body():
    case = SimpleNamespace(case_no=42)
    scenario = "Название: Synthetic service dispute\nPrivate fictional scenario and answer"
    assert _display_name(case, scenario) == "№42 · Synthetic service dispute"
    assert len(_display_name(case, "Название: " + "x" * 200)) <= 120


def test_ordinary_and_purged_cases_keep_the_existing_generic_name():
    case = SimpleNamespace(case_no=42)
    assert _display_name(case, None) == "Эталонный кейс №42"
    assert _display_name(case, "Fictional free-form scenario") == "Эталонный кейс №42"
