"""Letter sentences that restate the CV (plan 05d)."""
from services.career import cv_overlap as co

CV = ("PROFILE I'm a motivated Computer Science graduate (Lancaster, 2:1). "
      "• Found and fixed an authentication bypass during a security self-audit. "
      "• Map maker handles 10,000+ assets per layer at 60 FPS. Skills: Python")


def test_cv_units_split_on_sentences_and_bullets_and_drop_fragments():
    units = co.cv_units(CV)
    assert "Found and fixed an authentication bypass during a security self-audit." in units
    assert "Map maker handles 10,000+ assets per layer at 60 FPS." in units
    assert all(len(u.split()) >= co.MIN_UNIT_WORDS for u in units)


def test_letter_sentences_keep_paragraph_numbers_and_skip_greeting_and_sign_off():
    letter = ("Dear Acme,\n\nI'd like the Backend role at Acme. It suits me well enough.\n\n"
              "I found an auth bug in my own platform last spring.\n\nKind regards,\nLuke")
    out = co.letter_sentences(letter)
    assert [(p, s) for p, s in out] == [(1, "I'd like the Backend role at Acme."),
                                        (1, "It suits me well enough."),
                                        (2, "I found an auth bug in my own platform last spring.")]


def test_shared_run_finds_six_copied_words_ignoring_case_and_punctuation():
    a = "During a Security Self-Audit, I found and fixed an authentication bypass."
    assert co.shared_run(a, "Found and fixed an authentication bypass during a security self-audit.") \
        == "found and fixed an authentication bypass"


def test_shared_run_ignores_five_word_overlaps():
    assert co.shared_run("I fixed an authentication bypass today", "fixed an authentication bypass during") is None


def _fake_embed(table):
    def embed(texts):
        return [table.get(t, [0.0, 1.0]) for t in texts]
    return embed


BYPASS = "Found and fixed an authentication bypass during a security self-audit."
PARA = "In an audit of my own code I uncovered a way to skip login entirely."
WANT = "I want the role at Acme."  # embeds away from every CV unit


def test_flags_a_paraphrase_above_the_threshold():
    letter = f"Dear Acme,\n\nI want the role at Acme.\n\n{PARA}\n\nKind regards,\nLuke"
    flags = co.CvOverlap(CV, _fake_embed({BYPASS: [1.0, 0.0], PARA: [0.96, 0.28], WANT: [0.0, -1.0]}), threshold=0.9).flags(letter)
    assert len(flags) == 1
    assert flags[0]["paragraph"] == 2 and flags[0]["bullet"] == BYPASS and flags[0]["reason"] == "similar"
    assert flags[0]["similarity"] == 0.96


def test_opening_paraphrases_are_flagged_too():
    letter = f"Dear Acme,\n\nI want the role at Acme. {PARA}\n\nKind regards,\nLuke"
    flags = co.CvOverlap(CV, _fake_embed({BYPASS: [1.0, 0.0], PARA: [0.96, 0.28], WANT: [0.0, -1.0]}), threshold=0.9).flags(letter)
    assert [f["paragraph"] for f in flags] == [1]


def test_a_credentials_sentence_in_the_opening_is_exempt_from_similarity():
    degree = "I graduated from Lancaster University in 2025 with a 2:1 in Computer Science."
    profile = co.cv_units(CV)[0]
    letter = f"Dear Acme,\n\nI want the role at Acme. {degree}\n\nLater on, {degree}\n\nKind regards,\nLuke"
    embed = _fake_embed({profile: [1.0, 0.0], degree: [1.0, 0.0], f"Later on, {degree}": [1.0, 0.0], WANT: [0.0, -1.0]})
    flags = co.CvOverlap(CV, embed, threshold=0.9).flags(letter)
    assert [f["paragraph"] for f in flags] == [2]


def test_flags_copied_runs_even_in_the_opening():
    letter = "Dear Acme,\n\nI'm a motivated Computer Science graduate (Lancaster) keen on Acme.\n\nThanks,\nLuke"
    flags = co.CvOverlap(CV, _fake_embed({}), threshold=0.9).flags(letter)
    assert len(flags) == 1 and flags[0]["reason"] == "copied" and "computer science graduate" in flags[0]["shared"]


def test_without_an_embedder_only_copied_runs_are_flagged():
    letter = "Dear Acme,\n\nHi.\n\nI found and fixed an authentication bypass in my code.\n\nThanks,\nLuke"
    flags = co.CvOverlap(CV, None).flags(letter)
    assert [f["reason"] for f in flags] == ["copied"]


def test_cv_is_embedded_once_per_instance():
    calls = []

    def embed(texts):
        calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]
    ov = co.CvOverlap(CV, embed, threshold=2.0)
    ov.flags("Dear A,\n\nOne two three four five.\n\nSix seven eight nine ten.\n\nThanks,\nLuke")
    ov.flags("Dear A,\n\nOne two three four five.\n\nSix seven eight nine ten.\n\nThanks,\nLuke")
    assert calls[0] == len(co.cv_units(CV)) and calls.count(len(co.cv_units(CV))) == 1


def test_max_similarities_report_every_body_sentence_for_calibration():
    letter = "Dear A,\n\nI want the role at Acme today.\n\nI built a map maker in Rust.\n\nThanks,\nLuke"
    # Unlisted texts embed to [0, 1], so the first sentence matches every CV unit and the second none.
    sims = co.CvOverlap(CV, _fake_embed({"I built a map maker in Rust.": [1.0, 0.0]})).max_similarities(letter)
    assert sims == [(1, "I want the role at Acme today.", 1.0), (2, "I built a map maker in Rust.", 0.0)]


def test_flags_for_owner_reuses_the_embedded_cv(monkeypatch):
    calls = []

    units = set(co.cv_units(CV))

    def embed(texts):
        calls.append(list(texts))
        return [[0.0, 1.0] if t in units else [1.0, 0.0] for t in texts]
    monkeypatch.setattr(co, "_cache", {})
    monkeypatch.setattr(co, "full_cv_text", lambda owner: CV)
    monkeypatch.setattr(co, "default_embed", lambda: embed)
    letter = "Dear A,\n\nOpening line here.\n\nI found and fixed an authentication bypass in my code.\n\nThanks,\nLuke"
    first = co.flags_for_owner("vanders", letter)
    co.flags_for_owner("vanders", letter)
    assert [f["reason"] for f in first] == ["copied"]
    assert sum(1 for c in calls if c == co.cv_units(CV)) == 1


def test_flags_for_owner_without_a_cv_is_none(monkeypatch):
    monkeypatch.setattr(co, "full_cv_text", lambda owner: "")
    assert co.flags_for_owner("vanders", "Dear A,\n\nx.\n\nThanks") is None
