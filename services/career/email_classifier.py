"""Two-stage job-email classification: a free regex prefilter on the list
dict (subject + sender), then a JSON-only LLM verdict on the full message.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, Optional

KINDS = ("confirmation", "rejection", "interview_invite", "offer", "info_request", "recruiter_outreach", "other")
SENTIMENTS = ("positive", "negative", "neutral")

PREFILTER_SUBJECT_RE = re.compile(
    r"\b(application|applied|applying|interview|offer|unfortunately|position|role|candidate|"
    r"recruit\w*|talent|assessment|next steps?|your application|thank you for applying|"
    r"we(?:'d| would) like to)\b", re.I)
PREFILTER_SENDER_RE = re.compile(
    r"(greenhouse|lever\.co|ashby|workable|smartrecruiters|workday|myworkdayjobs|icims|bamboohr|"
    r"linkedin|indeed|otta|wellfound|teamtailor|personio|careers@|jobs@|recruit|talent|\bhr@)", re.I)
ATS_SENDER_RE = re.compile(
    r"(greenhouse|lever\.co|ashby|workable|smartrecruiters|workday|myworkdayjobs|icims|bamboohr|"
    r"linkedin|indeed|otta|wellfound|teamtailor|personio)", re.I)


def prefilter(list_dict: Dict[str, Any], extra_terms: Iterable[str] = ()) -> bool:
    subject = str(list_dict.get("subject") or "")
    sender = f"{list_dict.get('from_name') or ''} {list_dict.get('from_address') or ''}"
    if PREFILTER_SUBJECT_RE.search(subject) or PREFILTER_SENDER_RE.search(sender):
        return True
    hay = f"{subject} {sender}".lower()
    return any(t and t.lower() in hay for t in extra_terms)


def is_ats_sender(from_address: Optional[str]) -> bool:
    return bool(ATS_SENDER_RE.search(from_address or ""))


CLASSIFY_PROMPT = (
    "You classify emails for a software engineer's job search. Reply with ONE JSON object and nothing else:\n"
    '{"is_job_related": true|false, '
    '"kind": "confirmation|rejection|interview_invite|offer|info_request|recruiter_outreach|other", '
    '"sentiment": "positive|negative|neutral", '
    '"company": "<employer name or empty>", "role": "<job title or empty>", '
    '"confidence": <0.0-1.0>, "summary": "<=200 chars, what this email says>", '
    '"next_step": "<=120 chars, what the candidate should do, or empty"}\n'
    "Rules: confirmation = an application was received; rejection = not moving forward; "
    "interview_invite = any request to schedule a call/interview/assessment; offer = a job offer; "
    "info_request = they need something from the candidate; recruiter_outreach = unsolicited approach. "
    "Job alerts, newsletters and marketing are NOT job related. Never invent a company."
)

_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)
_DEFAULT = {"is_job_related": False, "kind": "other", "sentiment": "neutral", "company": "",
            "role": "", "confidence": 0, "summary": "", "next_step": ""}


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1")


def _as_pct(v: Any) -> int:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f < 2.0:   # 0-1 scale (models overshoot, e.g. 1.7); larger values are percentages
        f *= 100
    return int(round(max(0.0, min(100.0, f))))


def parse_verdict(text: str) -> Dict[str, Any]:
    out = dict(_DEFAULT)
    for candidate in reversed(_JSON_RE.findall(text or "")):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or ("is_job_related" not in data and "kind" not in data):
            continue
        out["is_job_related"] = _as_bool(data.get("is_job_related", False))
        out["kind"] = data.get("kind") if data.get("kind") in KINDS else "other"
        out["sentiment"] = data.get("sentiment") if data.get("sentiment") in SENTIMENTS else "neutral"
        out["company"] = str(data.get("company") or "").strip()[:120]
        out["role"] = str(data.get("role") or "").strip()[:120]
        out["confidence"] = _as_pct(data.get("confidence"))
        out["summary"] = str(data.get("summary") or "").strip()[:200]
        out["next_step"] = str(data.get("next_step") or "").strip()[:120]
        return out
    return out


async def classify(headers: Dict[str, str], body: str, complete: Callable, owner: Optional[str]) -> Dict[str, Any]:
    user = (
        f"From: {headers.get('from', '')}\nSubject: {headers.get('subject', '')}\n"
        f"Date: {headers.get('date', '')}\n\n{(body or '')[:6000]}"
    )
    raw = await complete([{"role": "system", "content": CLASSIFY_PROMPT},
                          {"role": "user", "content": user}], owner=owner)
    return parse_verdict(raw or "")
