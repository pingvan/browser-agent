import re
from dataclasses import dataclass
from typing import Any

import aioconsole
from colorama import Fore, Style

from src.parser.page_parser import InteractiveElement, PageState


@dataclass
class SecurityRule:
    name: str
    pattern: re.Pattern[str]
    description: str


_DANGEROUS_PATTERN_DEFS: list[tuple[str, str, str]] = [
    (
        "purchase",
        r"купить|buy|purchase|checkout|оформить\s*заказ|добавить\s*в\s*корзину|add\s*to\s*cart|pay|оплатить|оплата",
        "purchase/payment action",
    ),
    (
        "communication",
        r"отправить|send|submit|опубликовать|publish|post|написать|write\s*message|отправить\s*сообщение|comment",
        "send/submit/publish action",
    ),
    (
        "deletion",
        r"удалить|delete|remove|очистить|clear|drop|вы\s*уверены|are\s*you\s*sure|подтвердить\s*удаление",
        "delete/remove action",
    ),
    (
        "account",
        r"войти|sign\s*in|log\s*in|login|регистрация|register|sign\s*up|создать\s*аккаунт|create\s*account|выйти|log\s*out|logout",
        "account authentication/modification action",
    ),
    (
        "subscription",
        r"подписаться|subscribe|unsubscribe|отписаться|оформить\s*подписку|free\s*trial|пробный\s*период",
        "subscription action",
    ),
]

_NAVIGATE_URL_PATTERN: re.Pattern[str] = re.compile(
    r"checkout|payment|billing|pay\.", re.IGNORECASE
)

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(previous|all|prior)\s+instructions?",
        r"(forget|disregard)\s+(everything|all|previous)",
        r"you\s+are\s+now\s+(a|an|the)\b",
        r"your\s+(new\s+)?role\s+is",
        r"(override|bypass)\s+(previous|prior|all)\s+(instructions?|rules?|directives?)",
        r"\bSYSTEM\s*:",
        r"игнорир(уй|овать)\s+(предыдущие|все|инструкции)",
        r"забудь\s+(всё|все|предыдущие)",
        r"теперь\s+ты\s+(являешься|есть)\b",
        r"твоя\s+(новая\s+)?роль",
        r"(действуй|веди\s+себя)\s+как\b",
    ]
]


class SecurityLayer:
    def __init__(self) -> None:
        self._rules: list[SecurityRule] = [
            SecurityRule(
                name=name,
                pattern=re.compile(pattern, re.IGNORECASE),
                description=description,
            )
            for name, pattern, description in _DANGEROUS_PATTERN_DEFS
        ]

    def _find_element(self, ref: int, page_state: PageState) -> InteractiveElement | None:
        for el in page_state.elements:
            if el.ref == ref:
                return el
        return None

    def _check_text_dangerous(self, text: str) -> bool:
        for rule in self._rules:
            if rule.pattern.search(text):
                return True
        return False

    def is_dangerous(
        self, tool_name: str, args: dict[str, Any], page_state: PageState | None
    ) -> bool:
        if page_state is None:
            return False

        match tool_name:
            case "click" | "hover" | "select_option":
                ref: int = args.get("ref", -1)
                el = self._find_element(ref, page_state)
                if el is None:
                    return False
                combined = " ".join([el.text, el.aria_label, el.href])
                return self._check_text_dangerous(combined)

            case "type_text":
                if args.get("press_enter", False):
                    return True
                ref = args.get("ref", -1)
                el = self._find_element(ref, page_state)
                if el is None:
                    return False
                combined = " ".join([el.text, el.aria_label, el.placeholder])
                return self._check_text_dangerous(combined)

            case "navigate":
                url: str = args.get("url", "")
                return bool(_NAVIGATE_URL_PATTERN.search(url))

            case _:
                return False

    def _describe_action(self, tool_name: str, args: dict[str, Any]) -> str:
        match tool_name:
            case "click":
                return f"Click on element [{args.get('ref')}]"
            case "hover":
                return f"Hover over element [{args.get('ref')}]"
            case "select_option":
                return f"Select option '{args.get('value')}' in element [{args.get('ref')}]"
            case "type_text":
                text = str(args.get("text", ""))
                ref = args.get("ref")
                press_enter: bool = args.get("press_enter", False)
                suffix = " + press Enter (form submission)" if press_enter else ""
                return f"Type '{text[:50]}' into element [{ref}]{suffix}"
            case "navigate":
                return f"Navigate to '{args.get('url')}'"
            case _:
                return f"Execute {tool_name}({args})"

    async def request_confirmation(self, tool_name: str, args: dict[str, Any]) -> bool:
        description = self._describe_action(tool_name, args)
        _R = Style.RESET_ALL
        prompt = (
            f"{Fore.YELLOW}[SECURITY] Potentially dangerous action detected:\n"
            f"  {description}\n"
            f"Allow? [y/N]: {_R}"
        )
        try:
            answer = (await aioconsole.ainput(prompt)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes", "да")

    def check_prompt_injection(self, page_state: PageState) -> list[str]:
        matches: list[str] = []
        texts_to_check = [page_state.content]
        for el in page_state.elements:
            texts_to_check.extend([el.text, el.aria_label, el.placeholder])

        seen: set[str] = set()
        for text in texts_to_check:
            if not text:
                continue
            for pattern in _INJECTION_PATTERNS:
                m = pattern.search(text)
                if m:
                    key = pattern.pattern
                    if key not in seen:
                        seen.add(key)
                        matches.append(f"Matched '{pattern.pattern}': '{m.group(0)}'")
        return matches
