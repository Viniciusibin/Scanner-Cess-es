"""
remove_cessoes_antigas.py — Remove do database as cessoes com data anterior a 2020.

Para cada item de cada JSON em database/, remove de "classificacoes" as
entradas cuja data de cessao (campo "data_cessao" da classificacao, ou
"data_cessao_credito" do item como fallback) tem ano < 2020. Entradas sem
data conhecida (null, "Nao encontrado", formato invalido) sao mantidas, pois
nao ha como confirmar que ocorreram antes de 2020.

Itens que ficam sem nenhuma classificacao apos a remocao sao removidos do
arquivo. Uso:
    python remove_cessoes_antigas.py                    # aplica em todos os JSONs de database/
    python remove_cessoes_antigas.py <arquivo1> ...      # aplica so nos arquivos indicados
    python remove_cessoes_antigas.py --dry-run [arquivos]  # so mostra o que seria removido
"""

import glob
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(SCRIPT_DIR, "database")

ANO_LIMITE = 2020
DATA_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def ano_da_data(texto):
    if not texto:
        return None
    m = DATA_RE.match(str(texto).strip())
    if not m:
        return None
    return int(m.group(3))


def ano_da_classificacao(item, classificacao):
    bruto = classificacao.get("data_cessao") or item.get("data_cessao_credito")
    return ano_da_data(bruto)


def processar_arquivo(caminho, dry_run=False):
    with open(caminho, encoding="utf-8") as f:
        itens = json.load(f)

    novos_itens = []
    removidas = 0
    itens_removidos = 0

    for item in itens:
        classificacoes = item.get("classificacoes") or []
        mantidas = []
        for c in classificacoes:
            ano = ano_da_classificacao(item, c)
            if ano is not None and ano < ANO_LIMITE:
                removidas += 1
                continue
            mantidas.append(c)

        if classificacoes and not mantidas:
            itens_removidos += 1
            continue

        item["classificacoes"] = mantidas
        novos_itens.append(item)

    nome = os.path.basename(caminho)
    if removidas or itens_removidos:
        print(
            f"{nome}: -{removidas} cessao(oes) antiga(s), -{itens_removidos} item(ns) sem cessao restante, "
            f"total {len(itens)} -> {len(novos_itens)}"
        )
        if not dry_run:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(novos_itens, f, ensure_ascii=False, indent=2)
    else:
        print(f"{nome}: nada para remover")


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if args:
        arquivos = [a if os.path.isabs(a) else os.path.join(DATABASE_DIR, a) for a in args]
    else:
        arquivos = sorted(glob.glob(os.path.join(DATABASE_DIR, "*.json")))

    for caminho in arquivos:
        processar_arquivo(caminho, dry_run=dry_run)


if __name__ == "__main__":
    main()
