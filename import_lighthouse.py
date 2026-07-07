"""
import_lighthouse.py — Importa o export do Lighthouse (cessões de crédito) para o database/

Lê "Export - Lighthouse cessões de crédito.xlsx", agrupa as linhas por CNJ,
descobre o tribunal (TJ) de cada CNJ pelo código do CNJ, e grava um item por
processo no arquivo database/TJ-<UF>-2026.json correspondente — no mesmo
formato usado pelo restante do projeto (schema de TJ-GO-2026.json).

Campos que não existem na planilha ficam vazios (null/[]/""). Como um mesmo
CNJ pode ter várias linhas (várias cessões, com cedente/cessionário/valor/data
diferentes), cada linha se torna uma entrada na lista "classificacoes" do
item (que já é uma lista no schema original); "valor" e "data_cessao" ficam
dentro de cada entrada.

Para tribunais que já têm arquivo (MG, MS, MT, PR, RS), os processos cujo CNJ
já existe em qualquer arquivo desse tribunal (2025 ou 2026) são pulados, e os
novos são acrescentados ao arquivo "-2026". Para tribunais sem arquivo (BA,
PE, TO), cria um novo TJ-<UF>-2026.json.
"""

import glob
import json
import os
import re

import openpyxl

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(SCRIPT_DIR, "database")
XLSX_PATH = os.path.join(SCRIPT_DIR, "Export - Lighthouse cessões de crédito.xlsx")

CNJ_PATTERN = re.compile(r"^\d{7}-\d{2}\.(\d{4})\.(\d)\.(\d{2})\.\d{4}$")

TRIBUNAL_POR_CODIGO = {
    "05": "BA", "11": "MT", "12": "MS", "13": "MG",
    "16": "PR", "17": "PE", "21": "RS", "27": "TO",
}


def carregar_planilha():
    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
    ws = wb.active
    linhas = list(ws.iter_rows(values_only=True))[1:]  # pula cabeçalho
    return linhas


def formatar_valor(valor):
    if valor is None:
        return None
    return f"{float(valor):.2f}".replace(".", ",")


def formatar_data(data):
    if data is None:
        return None
    return data.strftime("%d/%m/%Y")


def montar_classificacao(linha):
    _, devedor_principal, todos_devedores, cedente, cessionario, valor, data = linha
    return {
        "is_cessao_real": True,
        "confianca": None,
        "resumo": None,
        "cedente": cedente,
        "cessionario": cessionario,
        "valor": formatar_valor(valor),
        "classe_credito": None,
        "cnj_rj": None,
        "recuperanda": devedor_principal or todos_devedores,
        "motivo_classificacao": None,
        "data_cessao": formatar_data(data),
    }


def montar_item(cnj, linhas):
    return {
        "id": None,
        "cnj": cnj,
        "classe": None,
        "orgao": None,
        "destinatarios": [],
        "link": None,
        "texto_completo": "",
        "keywords_cessao_fortes": [],
        "keywords_cessao_fracas": [],
        "keywords_rj": [],
        "classificacoes": [montar_classificacao(linha) for linha in linhas],
        "valor_causa": None,
        "data_cessao_credito": None,
    }


def agrupar_por_tribunal(linhas):
    """Retorna {uf: {cnj: [linhas...]}}."""
    por_uf = {}
    for linha in linhas:
        cnj = linha[0]
        m = CNJ_PATTERN.match(cnj)
        if not m:
            print(f"[AVISO] CNJ fora do padrao, ignorado: {cnj}")
            continue
        uf = TRIBUNAL_POR_CODIGO.get(m.group(3))
        if not uf:
            print(f"[AVISO] Tribunal desconhecido (codigo {m.group(3)}), ignorado: {cnj}")
            continue
        por_uf.setdefault(uf, {}).setdefault(cnj, []).append(linha)
    return por_uf


def cnjs_existentes(uf):
    """Uniao dos CNJs ja presentes em qualquer arquivo TJ-<uf>-*.json."""
    existentes = set()
    for caminho in glob.glob(os.path.join(DATABASE_DIR, f"TJ-{uf}-*.json")):
        with open(caminho, encoding="utf-8") as f:
            itens = json.load(f)
        existentes.update(item.get("cnj") for item in itens)
    return existentes


def main():
    linhas = carregar_planilha()
    por_uf = agrupar_por_tribunal(linhas)

    for uf, cnj_para_linhas in sorted(por_uf.items()):
        arquivo_2026 = os.path.join(DATABASE_DIR, f"TJ-{uf}-2026.json")
        ja_existe_arquivo = os.path.exists(arquivo_2026) or glob.glob(
            os.path.join(DATABASE_DIR, f"TJ-{uf}-*.json")
        )

        ja_processados = cnjs_existentes(uf) if ja_existe_arquivo else set()

        novos_itens = [
            montar_item(cnj, linhas_do_cnj)
            for cnj, linhas_do_cnj in cnj_para_linhas.items()
            if cnj not in ja_processados
        ]
        pulados = len(cnj_para_linhas) - len(novos_itens)

        if os.path.exists(arquivo_2026):
            with open(arquivo_2026, encoding="utf-8") as f:
                itens_atuais = json.load(f)
        else:
            itens_atuais = []

        itens_atuais.extend(novos_itens)

        with open(arquivo_2026, "w", encoding="utf-8") as f:
            json.dump(itens_atuais, f, ensure_ascii=False, indent=2)

        print(
            f"TJ-{uf}-2026.json: +{len(novos_itens)} novos, "
            f"{pulados} pulados (ja existiam), total {len(itens_atuais)}"
        )


if __name__ == "__main__":
    main()
