from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import unicodedata


_RULES_PATH = Path(__file__).with_name("account_name_rules.json")
_ALGORITHM_VERSION = "account-name-v2"
_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$")
_RESTRICTED_MESSAGE = "该账号名称不可用，请更换"
_TRADITIONAL_FOLD = str.maketrans(
    {
        "臺": "台",
        "獨": "独",
        "輪": "轮",
        "國": "国",
        "軍": "军",
        "黨": "党",
        "網": "网",
        "辦": "办",
        "檢": "检",
        "賭": "赌",
        "錢": "钱",
        "務": "务",
        "發": "发",
        "號": "号",
        "媽": "妈",
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "і": "i",
        "ј": "j",
        "к": "k",
        "м": "m",
        "т": "t",
        "в": "b",
        "н": "h",
        "0": "o",
        "1": "l",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
    }
)
_CJK_ALNUM_INFIX = re.compile(r"(?<=[\u3400-\u9fff])[a-z0-9]+(?=[\u3400-\u9fff])")
_ASCII_LATIN = re.compile(r"[a-z]")
_GREEK_OR_CYRILLIC = re.compile(r"[\u0370-\u052f\u1f00-\u1fff\u2de0-\u2dff\ua640-\ua69f]")


@dataclass(frozen=True)
class ValidatedAccountNames:
    username: str
    display_name: str
    policy_version: str


@dataclass(frozen=True)
class _RestrictedRule:
    rule_id: str
    mode: str
    terms: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...] = ()


class AccountNamePolicyError(ValueError):
    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        field: str,
        rule_id: str | None = None,
        policy_version: str | None = None,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.field = field
        self.rule_id = rule_id
        self.policy_version = policy_version


def _comparison_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold().translate(_TRADITIONAL_FOLD)
    normalized = _CJK_ALNUM_INFIX.sub("", normalized)
    return "".join(char for char in normalized if char.isalnum())


def _is_invisible_or_control(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.category(char).startswith("C")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _has_mixed_script_confusable(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return bool(_ASCII_LATIN.search(normalized) and _GREEK_OR_CYRILLIC.search(normalized))


def account_username_identity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return "".join(char for char in normalized if not _is_invisible_or_control(char))


@lru_cache(maxsize=1)
def _load_rules() -> tuple[str, tuple[_RestrictedRule, ...]]:
    try:
        payload = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("账号名称规则无法读取") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("账号名称规则格式不正确")
    version = payload.get("version")
    raw_rules = payload.get("rules")
    if not isinstance(version, str) or not version.strip() or not isinstance(raw_rules, list) or not raw_rules:
        raise RuntimeError("账号名称规则格式不正确")

    rules: list[_RestrictedRule] = []
    seen_ids: set[str] = set()
    seen_terms: set[tuple[str, str]] = set()
    for item in raw_rules:
        if not isinstance(item, dict):
            raise RuntimeError("账号名称规则格式不正确")
        raw_rule_id = item.get("id")
        mode = item.get("mode")
        raw_terms = item.get("terms")
        rule_id = raw_rule_id.strip() if isinstance(raw_rule_id, str) else ""
        if (
            not rule_id
            or rule_id in seen_ids
            or mode not in {"exact", "contains", "regex"}
            or not isinstance(raw_terms, list)
            or not raw_terms
        ):
            raise RuntimeError("账号名称规则格式不正确")
        if mode == "regex":
            normalized_terms = tuple(
                term.strip()
                for term in raw_terms
                if isinstance(term, str) and term.strip() and len(term) <= 200
            )
        else:
            normalized_terms = tuple(
                key
                for term in raw_terms
                if isinstance(term, str) and (key := _comparison_key(term))
            )
        if len(normalized_terms) != len(raw_terms):
            raise RuntimeError("账号名称规则存在空值")
        # 简繁体、全半角和大小写可能在归一化后成为同一项；合并这些
        # 等价写法，既保留规则表的可读性，也避免重复匹配。
        terms = tuple(dict.fromkeys(normalized_terms))
        matchers = {(mode, term) for term in terms}
        if any(matcher in seen_terms for matcher in matchers):
            raise RuntimeError("账号名称规则在不同类别中重复")
        try:
            patterns = tuple(re.compile(term) for term in terms) if mode == "regex" else ()
        except re.error as exc:
            raise RuntimeError("账号名称规则正则表达式不正确") from exc
        seen_terms.update(matchers)
        seen_ids.add(rule_id)
        rules.append(_RestrictedRule(rule_id=rule_id, mode=mode, terms=terms, patterns=patterns))
    rules_digest = hashlib.sha256(
        json.dumps(raw_rules, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"{version.strip()}+{_ALGORITHM_VERSION}+{rules_digest}", tuple(rules)


def load_account_name_policy() -> str:
    """Load and validate the complete rules table, including at application startup."""

    policy_version, _rules = _load_rules()
    return policy_version


def _validate_display_name(display_name: str, username: str) -> str:
    cleaned = unicodedata.normalize("NFC", display_name.strip() or username)
    if len(cleaned) > 50 or not cleaned.isprintable() or any(_is_invisible_or_control(char) for char in cleaned):
        raise AccountNamePolicyError(
            "display_name_format",
            "显示名称不能超过 50 个字符，且不能包含隐藏或控制字符",
            field="display_name",
        )
    return cleaned


def _reject_restricted_content(
    field: str,
    value: str,
    policy_version: str,
    rules: tuple[_RestrictedRule, ...],
) -> None:
    if _has_mixed_script_confusable(value):
        raise AccountNamePolicyError(
            "restricted_content",
            _RESTRICTED_MESSAGE,
            field=field,
            rule_id="mixed-script-confusable",
            policy_version=policy_version,
        )
    comparison_key = _comparison_key(value)
    for rule in rules:
        if rule.mode == "exact":
            matched = comparison_key in rule.terms
        elif rule.mode == "contains":
            matched = any(term in comparison_key for term in rule.terms)
        else:
            matched = any(pattern.search(comparison_key) for pattern in rule.patterns)
        if matched:
            raise AccountNamePolicyError(
                "restricted_content",
                _RESTRICTED_MESSAGE,
                field=field,
                rule_id=rule.rule_id,
                policy_version=policy_version,
            )


def validate_new_account_names(username: str, display_name: str | None = None) -> ValidatedAccountNames:
    """Validate names only when creating an account; existing-login behavior stays unchanged."""

    raw_username = str(username or "")
    if not _USERNAME_PATTERN.fullmatch(raw_username):
        raise AccountNamePolicyError(
            "username_format",
            "用户名须为 3—32 位小写字母、数字、点、下划线或短横线，且首尾须为字母或数字",
            field="username",
        )

    cleaned_display_name = _validate_display_name(str(display_name or ""), raw_username)
    policy_version, rules = _load_rules()
    _reject_restricted_content("username", raw_username, policy_version, rules)
    _reject_restricted_content("display_name", cleaned_display_name, policy_version, rules)
    return ValidatedAccountNames(
        username=raw_username,
        display_name=cleaned_display_name,
        policy_version=policy_version,
    )
