"""Deterministic cover-letter checks (plan 05a)."""
from services.career import letter_checks as lc

OPENING = "I'd like to apply for the Junior Developer role at PointWire, because your advert asks people to question code."
BODY = "I built a FastAPI service with Docker and tested it carefully before every release."
CLOSE = "I'm based in Kingston upon Thames and happy to travel to client sites.\n\nKind regards,\nLuke"


def _letter(body_words=280, opening=OPENING, close=CLOSE, greeting="Dear PointWire Hiring Team,"):
    filler = " ".join(["detail"] * body_words)
    return f"{greeting}\n\n{opening}\n\n{BODY} {filler}.\n\n{close}"


def _ctx(**over):
    base = dict(company="PointWire", role="Junior Developer",
                jd_text="Hybrid role with frequent travel to the South-West.",
                hard_requirements=["FastAPI", "Docker"])
    base.update(over)
    return lc.CheckContext(**base)


def _failed(letter, ctx=None):
    return {r.name: r.detail for r in lc.run_checks(letter, ctx or _ctx()) if not r.ok}


def test_a_compliant_letter_passes_every_check():
    assert _failed(_letter()) == {}


def test_too_short_letter_fails_length():
    assert "length" in _failed(_letter(body_words=150))


def test_too_long_letter_fails_length_and_names_the_overrun():
    detail = _failed(_letter(body_words=400))["length"]
    assert "400" in detail


def test_banned_phrase_fails_voice():
    assert "passionate" in _failed(_letter(opening=OPENING + " I am passionate about security."))["voice"]


def test_custom_banned_phrases_replace_the_defaults():
    letter = _letter(opening=OPENING + " Keen to dive deep.")
    assert "dive deep" in _failed(letter, _ctx(banned_phrases=["dive deep"]))["voice"]


def test_em_dash_and_exclamation_fail_voice():
    detail = _failed(_letter(opening=OPENING + " Security matters — a lot!"))["voice"]
    assert "em dash" in detail and "exclamation" in detail


def test_american_spelling_fails_voice():
    assert "optimize" in _failed(_letter(opening=OPENING + " I like to optimize code."))["voice"]


def test_opening_must_name_company_and_role():
    detail = _failed(_letter(opening="I have always liked writing software for people."))["names"]
    assert "PointWire" in detail and "Junior Developer" in detail


def test_greeting_does_not_count_as_the_opening():
    letter = _letter(opening="I have always liked writing software for people.")
    assert "names" in _failed(letter)


def test_another_company_name_fails_names():
    letter = _letter(opening=OPENING + " I admire what Axon does.")
    assert "Axon" in _failed(letter, _ctx(other_companies=["Axon", "PointWire Labs", "Arm"]))["names"]


def test_other_company_match_is_whole_word_and_case_sensitive():
    letter = _letter(opening=OPENING + " The arms race in security is real.")
    assert "names" not in _failed(letter, _ctx(other_companies=["Arm"]))


def test_logistics_required_when_the_advert_mentions_travel():
    assert "logistics" in _failed(_letter(close="Kind regards,\nLuke"))


def test_logistics_not_required_when_the_advert_is_silent():
    letter = _letter(close="Kind regards,\nLuke")
    assert "logistics" not in _failed(letter, _ctx(jd_text="Write Python services."))


def test_fewer_than_two_posting_specifics_fails():
    detail = _failed(_letter(), _ctx(hard_requirements=["Kubernetes", "Terraform"]))["specifics"]
    assert "Kubernetes" in detail and "Terraform" in detail


def test_warnings_lists_only_failed_details():
    results = lc.run_checks(_letter(body_words=150), _ctx())
    assert lc.warnings(results) == [r.detail for r in results if not r.ok]
    assert lc.warnings(results)


def test_long_requirement_phrases_match_on_their_key_words():
    ctx = _ctx(hard_requirements=["Degree or similar in Software Development",
                                  "One or more modern programming or scripting languages"])
    letter = _letter(opening=OPENING + " My degree covered software development in several programming languages.")
    assert "specifics" not in _failed(letter, ctx)


def test_requirement_with_few_key_words_present_still_fails():
    ctx = _ctx(hard_requirements=["Kubernetes cluster administration", "Terraform infrastructure modules"])
    assert "specifics" in _failed(_letter(), ctx)


def test_role_with_variants_matches_on_its_key_words():
    letter = _letter(opening="I'd like to join PointWire as a Software Engineer, because your advert asks people to question code.")
    assert "names" not in _failed(letter, _ctx(role="Junior/Mid Software Engineer"))


def test_banned_phrases_merge_defaults_with_the_owners_without_duplicates():
    out = lc.banned_phrases({"banned_phrases": ["dive deep", "Passionate", " "]})
    assert out[: len(lc.BANNED_PHRASES)] == list(lc.BANNED_PHRASES)
    assert out.count("dive deep") == 1 and len(out) == len(lc.BANNED_PHRASES) + 1


def test_banned_phrases_without_settings_are_the_defaults():
    assert lc.banned_phrases(None) == list(lc.BANNED_PHRASES)


def test_body_paragraphs_drop_greeting_sign_off_and_signature_lines():
    letter = ("Dear Acme,\n\nFirst point.\n\nSecond point.\n\nBest regards,\n\nLuke Van Der Veen\n\n"
              "https://github.com/lukevanderveen")
    assert lc.body_paragraphs(letter) == ["First point.", "Second point."]


def test_body_paragraphs_keep_a_short_final_sentence():
    assert lc.body_paragraphs("Dear Acme,\n\nFirst point.\n\nI'd welcome a chat.") == ["First point.", "I'd welcome a chat."]


def test_cv_overlap_check_is_skipped_without_flags():
    assert "cv_overlap" not in {r.name for r in lc.run_checks(_letter(), _ctx())}


def test_cv_overlap_check_passes_with_no_flags():
    assert {r.name: r.ok for r in lc.run_checks(_letter(), _ctx(cv_flags=[]))}["cv_overlap"]


def test_cv_overlap_check_names_each_restated_sentence():
    flags = [{"paragraph": 2, "sentence": "I fixed an auth bypass.", "bullet": "Found and fixed an auth bypass.",
              "reason": "similar", "shared": None, "similarity": 0.81},
             {"paragraph": 3, "sentence": "I'm a motivated graduate.", "bullet": "I'm a motivated graduate (2:1).",
              "reason": "copied", "shared": "i'm a motivated graduate", "similarity": None}]
    detail = _failed(_letter(), _ctx(cv_flags=flags))["cv_overlap"]
    assert "paragraph 2" in detail and "I fixed an auth bypass." in detail
    assert "paragraph 3" in detail and "copies" in detail and "reframe" in detail.lower()
