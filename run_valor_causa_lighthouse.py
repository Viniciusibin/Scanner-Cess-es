"""
run_valor_causa_lighthouse.py — Roda a busca de valor da causa (PDPJ) so nos
processos importados do Lighthouse (import_lighthouse.py), sem reprocessar os
itens que ja existiam nesses arquivos.

Um item e considerado "importado do Lighthouse" quando "classe" e None — so
esses itens tem esse campo vazio, ja que os demais foram preenchidos pelo
scraping original (DJEN/PDPJ).
"""

import json
import os
import time

from pdpj_valor_causa import buscar_valor_causa

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(SCRIPT_DIR, "database")

ARQUIVOS = [
    "TJ-BA-2026.json",
    "TJ-MG-2026.json",
    "TJ-MS-2026.json",
    "TJ-MT-2026.json",
    "TJ-PE-2026.json",
    "TJ-PR-2026.json",
    "TJ-RS-2026.json",
    "TJ-TO-2026.json",
]


def processar_arquivo(nome: str, pausa_seg: float = 0.3) -> None:
    caminho = os.path.join(DATABASE_DIR, nome)
    with open(caminho, encoding="utf-8") as f:
        itens = json.load(f)

    alvos = [item for item in itens if item.get("classe") is None and item.get("cnj")]
    print(f"\n--- {nome}: {len(alvos)} processo(s) importado(s) do Lighthouse ---")

    for i, item in enumerate(alvos, start=1):
        cnj = item["cnj"]
        print(f"[{i}/{len(alvos)}] {cnj} ...", end=" ", flush=True)
        resultado = buscar_valor_causa(cnj)
        item["valor_causa"] = resultado["valor"]

        if resultado["encontrado"]:
            print(f"OK — {resultado['valor']}")
        else:
            print(f"NAO ENCONTRADO — {resultado.get('_erro', '')}")

        if i < len(alvos):
            time.sleep(pausa_seg)

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(itens, f, ensure_ascii=False, indent=2)
    print(f"Arquivo atualizado: {caminho}")


def main() -> None:
    print("=" * 60)
    print("PDPJ — Valor da Causa (somente processos importados do Lighthouse)")
    print("=" * 60)
    for nome in ARQUIVOS:
        processar_arquivo(nome)


if __name__ == "__main__":
    main()
