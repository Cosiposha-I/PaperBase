# -*- coding: utf-8 -*-
"""
Сквозная нумерация рисунков в markdown-файле.

Ищет строки-подписи вида «*Рисунок ...*» или «***Рисунок N.** ...*», идущие
сразу после ![](...), и проставляет номера по порядку следования в документе.
Так номер не приходится держать в голове при каждой вставке.

Использование:
    python number_figures.py <файл.md> [--inplace]
"""
import argparse
import re

# подпись: строка, начинающаяся с ***Рисунок ...**  либо  *Рисунок ...
CAP = re.compile(r"^(\*{1,3})Рисунок(?:\s+\d+)?\.?\s*", re.M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--inplace", action="store_true")
    a = ap.parse_args()

    text = open(a.source, encoding="utf-8").read()

    n = 0

    def repl(m):
        nonlocal n
        n += 1
        stars = m.group(1)
        # держим формат ***Рисунок N.** — жирный номер, курсив подписи
        if len(stars) == 3:
            return f"***Рисунок {n}.** "
        return f"{stars}Рисунок {n}. "

    new = CAP.sub(repl, text)

    imgs = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", new))
    print(f"Подписей пронумеровано: {n}")
    print(f"Изображений в документе: {imgs}")
    if n != imgs:
        print("  ВНИМАНИЕ: число подписей и картинок не совпадает")

    if a.inplace:
        open(a.source, "w", encoding="utf-8").write(new)
        print(f"Записано: {a.source}")


if __name__ == "__main__":
    main()
