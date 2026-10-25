"""Decide whether a request text actually authorizes account/personal-data deletion.

Deterministic, multilingual cue matching. Explicit refusals of deletion and clearly
non-deletion intents (access, portability, correction, marketing opt-out, login help)
override deletion vocabulary, because those texts often mention deletion only to rule it out.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Explicit statements that deletion is NOT wanted.
NEGATED_DELETION = [
    r"\bnot (?:asking|requesting) (?:for )?(?:a |the )?(?:deletion|erasure|removal)",
    r"\bnot a request to (?:erase|delete|remove)",
    r"\b(?:do not|don'?t|dont|never) (?:delete|erase|remove|close)\b",
    r"\bwithout (?:removing|deleting|erasing|closing)\b",
    r"\b(?:keep|leave) (?:the|my) (?:account|profile)\b",
    r"\bleave the account (?:active|open|as it is)\b",
    r"\bcontinue using (?:the|this|my)? ?(?:service|account)\b",
    r"\bi still (?:use|need|want) (?:it|the account|this account|my account)\b",
    r"\bno (?:quiero|deseo) (?:eliminar|borrar|cerrar)",
    r"\bno (?:eliminen|borren|cierren|eliminéis|elimines|borres)\b",
    r"\bnão (?:quero|desejo) (?:excluir|apagar|encerrar)",
    r"\bnão (?:excluam|apaguem|encerrem|exclua|apague)\b",
    r"\bne (?:veux|souhaite) pas (?:supprimer|effacer|fermer)",
    r"\bne (?:supprimez|fermez|effacez) pas\b",
    r"削除しないで", r"삭제하지 마",
]

# Non-deletion intents. Each is decisive only when no unambiguous erasure verb targets the account.
NON_DELETION_INTENTS = {
    "access": [r"\bwhat personal information you hold\b", r"\binformation request\b",
               r"\bcopy of (?:the |my )?(?:information|data|personal data)\b", r"\bsubject access\b",
               r"\buna copia de (?:mis|los) datos\b", r"\buma cópia dos (?:meus )?dados\b",
               r"\bune copie de mes données\b"],
    "portability": [r"\bdownload my (?:account )?(?:information|data)\b", r"\bportable format\b",
                    r"\bcopy of last year'?s invoices\b"],
    "correction": [r"\bmisspelled\b", r"\bcorrect (?:it|my|the)\b", r"\bout of date\b",
                   r"\breplace it with\b", r"\bchange the email address\b", r"\bupdate my (?:phone|address|email)\b"],
    "marketing_opt_out": [r"\bmarketing lists? only\b", r"\bstop the promotional emails\b", r"\bunsubscribe\b",
                          r"\bopt(?:-| )?out of marketing\b"],
    "account_support": [r"\bresetting the login\b", r"\breset (?:my )?password\b", r"\bcan'?t access my profile\b"],
}

DELETION_CUES = [
    r"\bdelet(?:e|ed|es|ing|ion)\b", r"\berase(?:d|s)?\b", r"\berasure\b",
    r"\bremov(?:e|ed|al|ing)\b.{0,60}\b(?:account|profile|personal|data|record|records|details|search result|export|copy|copies)\b",
    r"\b(?:account|profile|personal|data|records?)\b.{0,60}\bremov(?:e|ed|al)\b",
    r"\bclose (?:my|this|the) account\b", r"\bclosed my account\b", r"\bprofile is gone\b",
    r"\bright to be forgotten\b", r"\bpermanent account[- ]deletion\b",
    # Spanish / Portuguese / French / German / Italian
    r"\beliminar\b", r"\beliminaci[oó]n\b", r"\bborrar\b", r"\bsupresi[oó]n\b",
    r"\bexclus[aã]o\b", r"\bexcluir\b", r"\bapagar\b",
    r"\bsuppression\b", r"\bsupprimer\b", r"\beffacer\b", r"\beffacement\b",
    r"\bl[oö]schen\b", r"\bl[oö]schung\b", r"\bcancellare\b", r"\bcancellazione\b",
    # Japanese / Korean / Chinese
    r"削除", r"삭제", r"删除", r"抹消",
]


@dataclass
class AuthorizationDecision:
    authorized: bool
    label: str
    cues: list = field(default_factory=list)
    confident: bool = True


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return text.replace("’", "'").replace("‘", "'").lower()


def classify_request(text: str) -> AuthorizationDecision:
    norm = _normalize(text)
    negations = [p for p in NEGATED_DELETION if re.search(p, norm)]
    intents = [name for name, pats in NON_DELETION_INTENTS.items() if any(re.search(p, norm) for p in pats)]
    deletion = [p for p in DELETION_CUES if re.search(p, norm)]

    if negations:
        return AuthorizationDecision(False, "explicit_non_deletion", negations + intents)
    if intents and not deletion:
        return AuthorizationDecision(False, intents[0], intents)
    if intents and deletion:
        # e.g. "remove me from marketing lists only" mentions removal but scopes it to marketing.
        scoped = any(re.search(p, norm) for p in NON_DELETION_INTENTS["marketing_opt_out"])
        if scoped:
            return AuthorizationDecision(False, "marketing_opt_out", intents + deletion)
        return AuthorizationDecision(True, "deletion_with_side_request", deletion + intents, confident=False)
    if deletion:
        return AuthorizationDecision(True, "deletion", deletion)
    return AuthorizationDecision(False, "no_deletion_cue", [], confident=False)
