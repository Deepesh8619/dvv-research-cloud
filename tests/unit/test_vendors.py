from types import SimpleNamespace

from src.engines.fielding.vendors import cost, flag_rates, pct, pct_removed, scorecard_row


def test_cost_and_pct_null_never_zero():
    assert cost(10, 2.5) == 25.0
    assert cost(10, None) is None
    assert cost(None, 2.5) is None
    assert cost(0, 2.5) == 0.0
    assert pct(5, 20) == 25.0
    assert pct(5, None) is None and pct(5, 0) is None


def test_flag_rates_and_removed():
    flags = {"a": [{"check": "speeder"}, {"check": "duplicate"}], "b": [{"check": "speeder"}]}
    assert flag_rates(4, flags) == {
        "speeder_rate": 50.0, "straightliner_rate": 0.0, "duplicate_rate": 25.0}
    assert flag_rates(0, {})["speeder_rate"] is None
    decisions = {"a": {"decision": "remove"}, "b": {"decision": "keep"}}
    assert pct_removed(["a", "b", "c", "d"], decisions) == 25.0
    assert pct_removed([], decisions) is None


def test_scorecard_row():
    link = SimpleNamespace(id="l1", vendor_name="V", cpi=None, target_completes=100)
    report = {"starts": 10, "dispositions": {"complete": 4}, "incidence_pct": 40.0,
              "median_loi_sec": 90.0, "dropoff_by_question": [{"qid": "Q2", "n": 3}]}
    row = scorecard_row(link, report, {"speeder_rate": 0.0}, None)
    assert row["cost_to_date"] is None and row["projected_cost"] is None
    assert row["pct_of_target"] == 4.0 and row["top_dropoff_question"] == "Q2"


def test_quota_balance_suggests_without_merging_ambiguous():
    from types import SimpleNamespace as N

    from src.engines.fielding.vendors import quota_balance

    def cell(lid, name, count, lim):
        return N(fielding_link_id=lid, name=name, count=count, cell_limit=lim)

    out = quota_balance(
        {"A": "Alpha", "B": "Beta"},
        [cell("A", "Male", 40, 50), cell("B", "Male", 5, 50),
         cell("A", "Dup", 1, 5), cell("A", "Dup", 2, 5), cell("B", "Open", 3, None)],
        100,
    )
    male = next(c for c in out["cells"] if c["cell"] == "Male")
    a, b = male["vendors"]
    assert male["combined_count"] == 45 and male["combined_limit"] == 100
    assert a["suggested_limit"] > a["limit"] and a["suggested_limit"] + b["suggested_limit"] == 100
    assert [x["name"] for x in out["ambiguous"]] == ["Dup"]
    assert all(c["cell"] != "Dup" for c in out["cells"])
    open_cell = next(c for c in out["cells"] if c["cell"] == "Open")
    assert open_cell["combined_limit"] is None and open_cell["vendors"][0]["limit"] is None
    assert "Nothing was changed" in out["note"]


def test_pid_deterministic_and_normalised():
    from src.engines.fielding.vendors import normalise_contact, pid_for, row_pid, unfinished

    assert normalise_contact(" A@X.com ", "98") == "a@x.com"
    assert normalise_contact("", "+91 (98)-76") == "919876"
    assert normalise_contact(None, "n/a") is None
    assert pid_for("s", "a@x.com") == pid_for("s", "a@x.com") != pid_for("t", "a@x.com")
    assert len(pid_for("s", "a")) == 16
    assert row_pid("s", {"email": " A@X.COM"}) == pid_for("s", "a@x.com")
    assert row_pid("s", {"name": "x"}) is None
    rows = [{"pid": "a"}, {"pid": "b"}, {"pid": None}]
    assert unfinished(rows, {"a"}) == [{"pid": "b"}]
