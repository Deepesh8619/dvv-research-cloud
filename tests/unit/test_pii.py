from src.core.models import DataMap, Variable, VarType
from src.engines.qc.pii import mask_text, pii_columns


def test_mask_text():
    out = mask_text("call me 9876543210 or a@b.co")
    assert "9876543210" not in out and "a@b.co" not in out
    assert mask_text("+91 98765-43210 ok") == "*** ok"
    assert mask_text("Q7 brand name: Acme, 5 stores, 2024-01-15") == (
        "Q7 brand name: Acme, 5 stores, 2024-01-15"
    )


def test_pii_columns_conservative():
    cols = {
        "email": "", "Q1": "Your e-mail", "Q2": "Mobile number", "Q3": "Contact number",
        "Q4": "Full name", "Q5": "Home address", "ip": "Raw IP address",
        "Zip": "Zip", "Q6": "Postal code", "Q7": "Brand name", "Q8": "Which mobile brand?",
        "Q9": "Which phone do you own", "Q10": "Age",
    }
    dm = DataMap()
    for n, lab in cols.items():
        dm.add(Variable(name=n, label=lab, type=VarType.TEXT))
    got = set(pii_columns(dm))
    assert got == {"email", "Q1", "Q2", "Q3", "Q4", "Q5", "ip"}


def test_email_ad_type_label_is_not_pii():
    dm = DataMap([
        Variable("DA_PLACEMENTr8", label="What types of ads has your company used? - E-mail",
                 type=VarType.SINGLE),
        Variable("em", label="Your email address", type=VarType.TEXT),
    ])
    assert pii_columns(dm) == ["em"]
