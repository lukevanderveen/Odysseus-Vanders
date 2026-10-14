"""Guards on reviewer line edits: placeholders and figures the CV doesn't support."""
from services.career import review_guards as rg

CV = "BSc Computer Science, Lancaster 2025, 2:1. Map maker handles 10,000+ assets per layer at 60 FPS."


def test_line_edits_parses_straight_and_curly_quotes():
    body = ('## Line edits\nreplace: "I built" → with: "I designed"\n'
            'replace: “tested it” -> with: “wrote tests first”')
    assert rg.line_edits(body) == [("I built", "I designed"), ("tested it", "wrote tests first")]


def test_flags_a_bracketed_placeholder():
    body = 'replace: "I graduated" → with: "I hold a [degree] in Software Development"'
    warnings = rg.unsupported_edit_warnings(body, CV)
    assert len(warnings) == 1 and "[degree]" in warnings[0]


def test_flags_a_figure_missing_from_the_cv():
    body = 'replace: "made it faster" → with: "cut load time by 40%"'
    warnings = rg.unsupported_edit_warnings(body, CV)
    assert len(warnings) == 1 and "40" in warnings[0]


def test_accepts_figures_from_the_cv_in_any_format():
    body = 'replace: "handles lots of assets" → with: "handles 10000 assets at 60 FPS"'
    assert rg.unsupported_edit_warnings(body, CV) == []


def test_accepts_figures_already_in_the_original_phrase():
    body = 'replace: "I have 3 years of rugby" → with: "I played rugby for 3 years"'
    assert rg.unsupported_edit_warnings(body, CV) == []


def test_review_without_line_edits_has_no_warnings():
    assert rg.unsupported_edit_warnings("## Verdict\nmaybe — fine.", CV) == []


def test_annotate_appends_a_tool_check_section():
    body = '## Line edits\nreplace: "x" → with: "a [metric] gain"'
    out = rg.annotate(body, CV)
    assert out.startswith(body) and "## Tool checks" in out and "[metric]" in out


def test_annotate_leaves_clean_reviews_untouched():
    body = '## Line edits\nreplace: "x" → with: "y"'
    assert rg.annotate(body, CV) == body


def test_flags_a_rewrite_that_breaks_the_voice_rules():
    body = 'replace: "I built" → with: "I am passionate about building — always!"'
    warnings = rg.unsupported_edit_warnings(body, CV, banned_phrases=["passionate"])
    assert len(warnings) == 1 and "passionate" in warnings[0] and "em dash" in warnings[0]


def test_voice_check_uses_the_given_banned_phrases():
    body = 'replace: "I built" → with: "I dive deep into problems"'
    assert rg.unsupported_edit_warnings(body, CV, banned_phrases=["dive deep"])
    assert rg.unsupported_edit_warnings(body, CV, banned_phrases=[]) == []
