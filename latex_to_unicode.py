# -*- coding: utf-8 -*-
"""
Преобразование LaTeX-формул ($$...$$) в Unicode-текст.

Зачем: конвертер md_to_docx.py не рендерит LaTeX — в .docx формулы попадают
сырым кодом вида `$$\\mu = \\frac{S}{K_S+S}$$`. Unicode-запись переносится
в любой редактор, ищется поиском и копируется, в отличие от картинок.

Использование:
    python latex_to_unicode.py <файл.md> [--inplace] [--check]
"""
import argparse
import re
import sys

SUB = str.maketrans("0123456789+-=()aehijklmnoprstuvx",
                    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")
SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")

GREEK = {
    r"\mu": "μ", r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\Delta": "Δ", r"\sigma": "σ", r"\omega": "ω", r"\lambda": "λ", r"\rho": "ρ",
    r"\tau": "τ", r"\phi": "φ", r"\pi": "π", r"\epsilon": "ε", r"\theta": "θ",
}

OPS = {
    r"\rightarrow": "→", r"\to": "→", r"\leftarrow": "←",
    r"\Rightarrow": "⇒", r"\leftrightarrow": "↔",
    r"\cdot": "·", r"\times": "×", r"\approx": "≈", r"\neq": "≠",
    r"\leq": "≤", r"\geq": "≥", r"\ll": "≪", r"\gg": "≫",
    r"\pm": "±", r"\infty": "∞", r"\partial": "∂", r"\sum": "Σ",
}


def _script(text: str, table, fallback_prefix: str) -> str:
    """Переводит в под/надстрочные символы; что не переводится — через префикс."""
    out = []
    for ch in text:
        conv = ch.translate(table)
        if conv != ch or ch == " ":
            out.append(conv)
        else:
            # символа нет в Unicode-наборе — оставляем обычным
            out.append(ch)
    res = "".join(out)
    # если ничего не сконвертировалось и это буквы — помечаем префиксом
    if res == text and text.isalpha() and len(text) > 0:
        return fallback_prefix + text
    return res


def _take_group(s: str, i: int):
    """Читает {...} с учётом вложенности начиная с позиции i (s[i] == '{').

    Возвращает (содержимое, индекс_после_закрывающей_скобки).
    Нужен потому, что регулярка [^{}]* ломается на вложенных скобках
    вида \\frac{0{,}69}{\\mu} или \\frac{\\mu_m}{2\\sqrt{...}}.
    """
    assert s[i] == "{"
    depth, j = 0, i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return s[i + 1:], len(s)  # незакрытая скобка


def _expand_cmd(s: str, cmd: str, nargs: int, build) -> str:
    """Разворачивает команду \\cmd{..}{..} с учётом вложенных скобок."""
    out, i, token = [], 0, "\\" + cmd
    while i < len(s):
        if s.startswith(token, i) and i + len(token) < len(s) and s[i + len(token)] == "{":
            args, j = [], i + len(token)
            ok = True
            for _ in range(nargs):
                if j >= len(s) or s[j] != "{":
                    ok = False
                    break
                arg, j = _take_group(s, j)
                args.append(arg)
            if ok:
                out.append(build(*args))
                i = j
                continue
        out.append(s[i])
        i += 1
    return "".join(out)


def convert(expr: str) -> str:
    """LaTeX-выражение -> Unicode-строка."""
    s = expr.strip()

    # десятичная запятая: 109{,}2 -> 109,2 (ДО разбора дробей, иначе ломает скобки)
    s = s.replace("{,}", ",")

    # \text{...}, \textit{...}, \mathrm{...} -> содержимое
    for cmd in ("textit", "textbf", "textrm", "mathrm", "text"):
        s = _expand_cmd(s, cmd, 1, lambda a: a)

    # \xrightarrow{подпись} -> —(подпись)→
    s = _expand_cmd(s, "xrightarrow", 1, lambda a: f" —({a.strip()})→ ")

    # \sqrt{...} -> √(...)   (до дробей: дробь может содержать корень)
    s = _expand_cmd(s, "sqrt", 1, lambda a: "√(" + a.strip() + ")")

    # \frac{a}{b} -> a/b, со скобками если многочлен
    def frac(num, den):
        num, den = num.strip(), den.strip()
        if re.search(r"[+\-−]", num) and not (num.startswith("(") and num.endswith(")")):
            num = f"({num})"
        if re.search(r"[+\-−]", den) and not (den.startswith("(") and den.endswith(")")):
            den = f"({den})"
        return f"{num}/{den}"

    for _ in range(5):  # вложенные дроби
        new = _expand_cmd(s, "frac", 2, frac)
        if new == s:
            break
        s = new

    # греческие буквы и операторы
    for k, v in sorted(GREEK.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(k, v)
    for k, v in sorted(OPS.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(k, v)

    # \begin{cases}...\end{cases} -> через «;», выравниватель & убираем
    s = re.sub(r"\\begin\{cases\}(.*?)\\end\{cases\}",
               lambda m: m.group(1).replace(r"\\", ";  ").replace("&", ""), s, flags=re.S)

    # \quad, \, \; \! -> пробелы
    s = re.sub(r"\\(?:quad|qquad)", "   ", s)
    s = re.sub(r"\\[,;! ]", " ", s)

    # индексы: _{...} и _x
    s = re.sub(r"_\{([^{}]*)\}", lambda m: _script(m.group(1), SUB, "_"), s)
    s = re.sub(r"_([A-Za-zА-Яа-я0-9])", lambda m: _script(m.group(1), SUB, "_"), s)

    # степени: ^{...} и ^x  (^* -> звёздочка обычная: S^* -> S*)
    s = re.sub(r"\^\{([^{}]*)\}", lambda m: _script(m.group(1), SUP, "^"), s)
    s = s.replace("^*", "*")
    s = re.sub(r"\^([A-Za-z0-9+\-])", lambda m: _script(m.group(1), SUP, "^"), s)

    # чистка остатков
    s = s.replace("\\", "").replace("{", "").replace("}", "")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def process(text: str):
    """Заменяет все $$...$$ на Unicode. Возвращает (новый текст, список замен)."""
    D = re.escape("$" + "$")
    changes = []

    def repl(m):
        src = m.group(1)
        # несколько формул подряд внутри одного блока разделяем по переводу строки
        parts = [p for p in src.split("\n") if p.strip()]
        outs = [convert(p) for p in parts]
        res = "\n\n".join(outs)
        changes.append((src.strip()[:70], res[:70]))
        return res

    new = re.sub(D + r"(.+?)" + D, repl, text, flags=re.S)
    return new, changes


def main():
    ap = argparse.ArgumentParser(description="LaTeX ($$...$$) -> Unicode в markdown-файле")
    ap.add_argument("source")
    ap.add_argument("--inplace", action="store_true", help="переписать файл")
    ap.add_argument("--check", action="store_true", help="только показать, что будет заменено")
    a = ap.parse_args()

    text = open(a.source, encoding="utf-8").read()
    new, changes = process(text)

    print(f"Формул найдено: {len(changes)}")
    for src, dst in changes:
        print(f"  {src}")
        print(f"    -> {dst}")

    if a.check:
        return
    if a.inplace:
        open(a.source, "w", encoding="utf-8").write(new)
        print(f"\nЗаписано: {a.source}")
    else:
        sys.stdout.write(new)


if __name__ == "__main__":
    main()
