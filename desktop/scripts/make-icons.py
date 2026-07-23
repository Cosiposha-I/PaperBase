"""Генерирует build/icon.ico (Windows) и промежуточные PNG для icon.icns (macOS)
из одного исходного PNG. Запускать один раз при смене иконки: python scripts/make-icons.py
"""
import sys
from pathlib import Path
from PIL import Image

BUILD = Path(__file__).parent.parent / "build"
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else BUILD / "icon-source.png"
BUILD.mkdir(exist_ok=True)

img = Image.open(SRC).convert("RGBA")
print(f"Исходник: {SRC} ({img.size[0]}x{img.size[1]})")

# --- Windows .ico: набор размеров в одном файле ---
ico_sizes = [16, 24, 32, 48, 64, 128, 256]
img.save(BUILD / "icon.ico", format="ICO", sizes=[(s, s) for s in ico_sizes])
print(f"Записано: {BUILD / 'icon.ico'} (размеры: {ico_sizes})")

# --- macOS .icns: Pillow умеет писать ICNS напрямую (без macOS-утилиты iconutil) ---
try:
    img.save(BUILD / "icon.icns", format="ICNS")
    print(f"Записано: {BUILD / 'icon.icns'}")
except Exception as e:
    # Запасной путь: iconset для сборки через iconutil на macOS-раннере CI.
    print(f"Pillow не смог записать .icns напрямую ({e}) — готовлю iconset для iconutil")
    iconset = BUILD / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for s in [16, 32, 64, 128, 256, 512, 1024]:
        img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
        if s <= 512:
            img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
    print(f"Записано: {iconset} (собрать: iconutil -c icns {iconset} -o {BUILD / 'icon.icns'})")
