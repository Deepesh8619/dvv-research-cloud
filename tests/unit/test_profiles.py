

def test_an_unnumbered_base_tagged_document_is_recognised():
    from src.engines.questionnaire.profiles import detect_profile
    from src.intake.questionnaire_loader import DocumentBlock

    blocks = [DocumentBlock("paragraph", f"[BASE: ALL] Question {i}?", i) for i in range(5)]
    assert detect_profile(blocks).profile.slug == "base_tagged"
