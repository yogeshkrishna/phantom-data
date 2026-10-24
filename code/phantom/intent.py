"""Conservative, explainable erasure authorization with an ambiguity hook."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class IntentDecision:
    authorization: Literal["REQUESTED", "NOT_REQUESTED", "AMBIGUOUS"]
    reason: str
    language: str | None = None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'").replace("‘", "'")
    # Fold Latin accents without decomposing Korean syllables or Japanese text.
    return "".join("".join(c for c in unicodedata.normalize("NFKD", char)
                           if not unicodedata.combining(c))
                   if "LATIN" in unicodedata.name(char, "") else char for char in text)


def classify_intent(request_text: str) -> IntentDecision:
    """Classify consent, never using reported workflow status or case identifiers.

    This deterministic path covers explicit EN/FR/ES/PT/JA/KO requests. Unclear,
    contradictory or unrecognized erasure language is exposed for later review,
    not treated as permission. Local model assistance can consume AMBIGUOUS later.
    """
    if not isinstance(request_text, str):
        raise ValueError("request_text must be a string")
    if not request_text.strip():
        return IntentDecision("AMBIGUOUS", "MISSING_REQUEST_TEXT")
    text = _normalize(request_text)
    text = re.sub(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\x27[^\x27\n]*\x27(?!\w)', " ", text)
    clauses = [part.strip() for part in re.split(r"[.!?;。！？\n]+", text) if part.strip()]
    scope_pattern = r"\b(account|profile|personal[ -]+(?:data|information|details)|my data|contact details|my name|records|export file|search result)\b"
    global_scope = bool(re.search(scope_pattern, text))
    prior_erasure = bool(re.search(r"\b(?:delet\w*|eras\w*|remov\w*|closed|closure)\b|\b(?:account|profile)\b.{0,45}\bgone\b", text))
    positive, negative = [], []
    mentions_erasure = False
    for clause in clauses:
        english_scope = bool(re.search(scope_pattern, clause))
        english_action = bool(re.search(r"\b(delet\w*|eras\w*|remov\w*|purge|wipe)\b", clause))
        closure_action = bool(re.search(r"\b(?:close|closure|deactivate)\b", clause))
        mentions_erasure |= english_action and english_scope
        mentions_erasure |= english_scope and closure_action
        english_denial = re.search(
            r"\b(?:do not|don't|not asking|not requesting|not a request|never|without|no need to)\b"
            r"(?:(?!\b(?:but|and)\b)[^,;.!?]){0,45}"
            r"\b(?:delet\w*|eras\w*|remov\w*|purge|wipe|close|deactivate)\b|"
            r"\b(?:cancel|withdraw|revoke)\b.{0,40}\b(?:deletion|erasure)\b", clause)
        informational = re.search(r"\b(?:how (?:do|can|to)|status of|deletion status|"
                                  r"deletion policy|whether|already deleted)\b", clause)
        english_request = re.search(
            r"(?:^|\bplease\s+|\bkindly\s+|\b(?:can|could|would) you\s+)"
            r"(?:permanently\s+)?(?:delete|erase|remove|purge|wipe)\b|"
            r"\b(?:want|wish|would like|am asking|ask)\b.{0,80}\b(?:delete|erase|remove|deleted|erased|removed)\b|"
            r"\brequest(?:ing)?\s+(?:permanent\s+)?(?:deletion|erasure)\b|"
            r"\brequest\s+to\s+(?:delete|erase|remove)\b|"
            r"\band\s+(?:delete|erase|remove)\b|"
            r"\b(?:process|begin|treat)\b.{0,65}\b(?:deletion|account-deletion)\b", clause)
        continuation = global_scope and prior_erasure and bool(re.search(
            r"\b(?:please|can you|could you)\b.{0,65}\b(?:check|verify|audit|inspect|confirm|investigate|finish|complete|handle)\b|"
            r"\b(?:check|verify|audit|inspect|confirm|investigate|finish|complete)\b.{0,65}\b(?:deletion|deleted|removed|gone)\b", clause))
        permanent_closure = global_scope and bool(re.search(r"\bclose\b.{0,45}\b(?:permanently|completely)\b", clause))
        declared_erasure = english_scope and bool(re.search(r"\bi(?:'m| am) deleting\b", clause))
        if english_denial and global_scope:
            negative.append("en")
        if not english_denial and (continuation or permanent_closure or declared_erasure):
            positive.append("en")
        if english_scope and (english_action or closure_action):
            if english_denial:
                negative.append("en")
            elif english_action and english_request and not informational:
                positive.append("en")
        elif global_scope and english_action and english_request and not english_denial and not informational and re.search(r"\b(?:it|them|those|these|both addresses|the rest)\b", clause):
            positive.append("en")  # A follow-up clause may use 'that result' or 'both addresses'.

        # Language-level vocabulary and consent markers, never complete templates.
        languages = (
            ("fr", r"\bcompte\b|donnees personnelles", r"suppr\w*|effac\w*",
             r"demande|souhaite|veuillez|s'il vous plait", r"\bne\b.{0,45}\bpas\b|sans supprimer|annul\w*"),
            ("es", r"\bcuenta\b|datos personales", r"elimin\w*|borr\w*",
             r"solicito|quiero|deseo|por favor|completar", r"\bno\b.{0,35}(?:elimin|borr)|cancel\w*"),
            ("pt", r"\bconta\b|dados pessoais", r"exclu\w*|apag\w*|elimin\w*",
             r"solicito|quero|desejo|por favor", r"\bnao\b.{0,35}(?:exclu|apag|elimin)|cancel\w*"),
            ("ja", r"アカウント|個人情報|個人データ", r"削除|消去",
             r"依頼|お願い|希望|要求|してください", r"削除しない|消去しない|希望しません|キャンセル"),
            ("ko", r"계정|개인정보|개인 정보", r"삭제|제거",
             r"요청|원합니다|해주세요|해 주세요", r"삭제하지|제거하지|원하지|취소"),
        )
        for language, scope, action, consent, denial in languages:
            if re.search(scope, text) and re.search(action, clause):
                mentions_erasure = True
                if re.search(denial, clause):
                    negative.append(language)
                elif re.search(consent, clause):
                    positive.append(language)
    if positive and negative:
        return IntentDecision("AMBIGUOUS", "CONFLICTING_ERASURE_INSTRUCTIONS")
    if positive:
        return IntentDecision("REQUESTED", "EXPLICIT_ERASURE_REQUEST", positive[0])
    if negative:
        return IntentDecision("NOT_REQUESTED", "ERASURE_DECLINED_OR_WITHDRAWN", negative[0])
    if mentions_erasure:
        if re.search(r"\b(?:how|status|policy|already)\b", text):
            return IntentDecision("NOT_REQUESTED", "ERASURE_INFORMATION_ONLY")
        return IntentDecision("AMBIGUOUS", "ERASURE_MENTION_WITHOUT_CLEAR_AUTHORIZATION")
    if not re.search(r"\b(?:i|my|please|could|can|account|profile|information|email|emails|"
                     r"records|help|hello|thanks|marketing|download|copy|request)\b", text):
        return IntentDecision("AMBIGUOUS", "UNRECOGNIZED_REQUEST_LANGUAGE")
    return IntentDecision("NOT_REQUESTED", "NO_ACCOUNT_OR_PERSONAL_DATA_ERASURE_REQUEST")
