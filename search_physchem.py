# -*- coding: utf-8 -*-
r"""Батч-поиск под курсовую по физхимии (Ti3C2, ЦВА+ВДЭ): модель bge-m3 грузится
ОДИН раз, прогоняется набор запросов по подтемам обзора, результат — JSON.

Тема задаётся env (PAPERBASE_CORPUS_DIR / PAPERBASE_DATA_DIR = data-Физхимия).
Инструмент только ищет — синтез обзора делает Claude поверх этого JSON.

Запуск из папки paperbase (GPU):
    $env:PAPERBASE_CORPUS_DIR="...Физхимия/Литература"; $env:PAPERBASE_DATA_DIR="data-Физхимия"
    .\.venv\Scripts\python.exe search_physchem.py <out.json>
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paperbase.config import load_config
from paperbase.store import Store
from paperbase.embed import Embedder
from paperbase.query import search

QUERIES = [
    # --- Методы (ЭХМА) ---
    ("cv_method", "циклическая вольтамперометрия принцип метод развёртка потенциала пик тока", 12),
    ("cv_theory", "Randles Sevcik peak current scan rate diffusion reversible cyclic voltammetry", 10),
    ("rde_method", "вращающийся дисковый электрод уравнение Левича предельный диффузионный ток конвекция", 10),
    ("rde_levich", "rotating disk electrode Levich Koutecky mass transport kinetic current hydrodynamic", 10),
    ("electrocatalysis", "электрокатализ перенапряжение обменный ток вулкан дескриптор энергия адсорбции", 10),
    # --- MXenes: структура и синтез ---
    ("mxene_what", "MXene two-dimensional transition metal carbide general formula MAX phase exfoliation", 10),
    ("ti3c2_structure", "Ti3C2 structure surface terminations Tx layered accordion morphology", 8),
    ("mxene_hf", "selective etching HF hydrofluoric acid aluminum Ti3AlC2 MXene synthesis", 10),
    ("mxene_lifhcl", "LiF HCl in situ etching Ti3C2 clay MXene minimally intensive layer delamination", 8),
    ("mxene_bifluoride", "NH4HF2 ammonium bifluoride etching MXene transparent conductive film", 8),
    ("mxene_lewis", "Lewis acid molten salt etching MXene ZnCl2 CuCl2 non-aqueous chloride termination", 8),
    ("mxene_ndoped", "nitrogen doped Ti3C2 colloidal sheets electrochemical performance", 6),
    # --- MXenes как электрокатализаторы ---
    ("her_mxene", "MXene Ti3C2 hydrogen evolution reaction overpotential Tafel slope HER electrocatalyst", 12),
    ("her_seh", "Mo2C MXene hydrogen evolution efficient electrocatalyst free energy hydrogen adsorption", 8),
    ("her_akir", "1T MoS2 Ti3C2 heterojunction hydrogen evolution overpotential Tafel current density", 8),
    ("oer_watersplit", "oxygen evolution reaction OER water splitting electrocatalyst overpotential bifunctional", 10),
    ("orr_mxene", "oxygen reduction reaction ORR MXene carbon electrocatalyst onset potential half wave", 10),
    ("co2rr", "carbon dioxide reduction CO2RR electrocatalyst faradaic efficiency selectivity", 8),
    # --- Суперконденсаторы на активированном угле ---
    ("sc_principle", "суперконденсатор двойной электрический слой EDLC ёмкость электрод накопление энергии", 10),
    ("ac_synthesis", "активированный уголь синтез карбонизация активация KOH биомасса пиролиз пористость", 12),
    ("ac_biomass", "biomass derived porous carbon rice husk peanut shell activation surface area pore", 12),
    ("ndoping_sc", "nitrogen oxygen self doped porous carbon supercapacitor pseudocapacitance heteroatom", 8),
    ("cv_capacitance", "specific capacitance cyclic voltammetry scan rate rectangular shape supercapacitor calculation formula", 12),
    ("redox_electrolyte", "redox active electrolyte ferricyanide supercapacitor capacitance enhancement three electrode", 8),
    ("gcd", "galvanostatic charge discharge specific capacitance energy density power density supercapacitor", 8),
]

def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "physchem_out.json"
    cfg = load_config()
    print("data_dir =", cfg.data_dir, file=sys.stderr)
    with Store(cfg) as store:
        emb = Embedder(cfg)
        out = {}
        for key, q, k in QUERIES:
            hits = search(store, emb, q, k=k)
            out[key] = {"query": q, "hits": [h.to_dict() for h in hits]}
            print(f"[{key}] {len(hits)} hits", file=sys.stderr)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    print("written", os.path.abspath(out_path), file=sys.stderr)

if __name__ == "__main__":
    main()
