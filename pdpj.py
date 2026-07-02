"""
pdpj_valor_causa.py — Busca o valor da causa a partir da capa do processo na PDPJ
===================================================================
Uso:
    python pdpj_valor_causa.py <CNJ>
    python pdpj_valor_causa.py                 # usa CNJ_ALVO abaixo

Estratégia:
    1. GET /api/v2/processos/{cnj} (via pdpj.buscar_processo_completo) → capa do processo.
    2. Tenta um conjunto de key-paths conhecidos (raiz e tramitacaoAtual) onde o
       valor da causa costuma aparecer nos sistemas agregados pela PDPJ (PJe, eproc,
       Projudi, SAJ...) — o campo NÃO é padronizado entre tribunais/graus.
    3. Fallback: varre recursivamente todo o JSON da capa procurando qualquer chave
       que contenha "valor" e reporta os candidatos encontrados, para conferência
       manual quando os key-paths conhecidos não batem.
    4. Normaliza o valor (ex.: "R$ 1.234.567,89" → 1234567.89) e imprime/salva.

Importante:
    Nem todo processo tem esse campo preenchido na capa — muitos sistemas de
    origem não repassam "valor da causa" na integração com a PDPJ. Quando isso
    acontecer, o valor só estará disponível lendo o texto da petição inicial
    (ver PROMPT_ANALISE / PROMPT_VARREDURA em pdpj.py).

Gera:
    valor_causa/<cnj_safe>.json
"""

from __future__ import annotations

import json
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
import pdpj  # noqa: E402  (reaproveita token/headers/HTTP da PDPJ)

# ============================================================
# CONFIGURACAO
# ============================================================

CNJ_ALVO = "1028324-52.2015.8.26.0100"

PASTA_SAIDA = os.path.join(_SCRIPT_DIR, "valor_causa")

# Key-paths conhecidos onde o valor da causa costuma aparecer na capa da PDPJ.
# "raiz" = nivel raiz do objeto de processo; "tramitacaoAtual" = sub-objeto onde
# hoje ja lemos partes e documentos (pdpj.py / pdpj_capa.py).
CANDIDATOS_VALOR: list[tuple[str, str]] = [
    ("raiz", "valor"),
    ("raiz", "valorCausa"),
    ("raiz", "valorAcao"),
    ("raiz", "valorDaCausa"),
    ("tramitacaoAtual", "valor"),
    ("tramitacaoAtual", "valorCausa"),
    ("tramitacaoAtual", "valorAcao"),
    ("tramitacaoAtual", "valorDaCausa"),
]


# ============================================================
# NORMALIZACAO DE VALOR MONETARIO
# ============================================================

def _parsear_valor_monetario(bruto: object) -> float | None:
    """Converte um valor bruto (numero ou string 'R$ 1.234.567,89') em float."""
    if bruto is None:
        return None
    if isinstance(bruto, (int, float)):
        return float(bruto)

    texto = re.sub(r"[^\d,.\-]", "", str(bruto)).strip()
    if not texto:
        return None

    if "," in texto and "." in texto:
        # Formato BR: "1.234.567,89" -> "1234567.89"
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        # Só vírgula: assume separador decimal BR -> "1234567,89" -> "1234567.89"
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return None


# ============================================================
# BUSCA NA CAPA
# ============================================================

def _buscar_por_candidatos(processo: dict) -> tuple[str, object] | None:
    """Tenta os key-paths conhecidos, na ordem. Retorna (caminho, valor_bruto) ou None."""
    tramitacao_atual = processo.get("tramitacaoAtual") or {}
    for origem, chave in CANDIDATOS_VALOR:
        alvo = processo if origem == "raiz" else tramitacao_atual
        bruto = alvo.get(chave)
        if bruto not in (None, "", 0):
            caminho = chave if origem == "raiz" else f"tramitacaoAtual.{chave}"
            return caminho, bruto
    return None


def _varrer_chaves_valor(
    obj: object, prefixo: str = "", prof: int = 0, max_prof: int = 6
) -> list[tuple[str, object]]:
    """Fallback: varre recursivamente o JSON procurando qualquer chave que contenha 'valor'."""
    if prof >= max_prof:
        return []
    achados: list[tuple[str, object]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            caminho = f"{prefixo}.{k}" if prefixo else k
            if "valor" in k.lower() and not isinstance(v, (dict, list)):
                achados.append((caminho, v))
            achados.extend(_varrer_chaves_valor(v, caminho, prof + 1, max_prof))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            achados.extend(_varrer_chaves_valor(item, f"{prefixo}[{i}]", prof + 1, max_prof))
    return achados


def buscar_valor_causa(cnj: str, processo: dict | None = None) -> dict:
    """
    Busca o valor da causa de um processo pelo CNJ, a partir da capa (JSON de metadados).

    Retorna dict com:
        cnj, encontrado, caminho (key-path onde achou), valor_bruto, valor (float),
        candidatos_alternativos (outras chaves "valor*" achadas, para conferencia).
    """
    if processo is None:
        processo = pdpj.buscar_processo_completo(cnj)

    if not processo:
        return {
            "cnj": cnj,
            "encontrado": False,
            "caminho": None,
            "valor_bruto": None,
            "valor": None,
            "candidatos_alternativos": [],
            "_erro": "Processo nao encontrado na PDPJ (capa).",
        }

    resultado = _buscar_por_candidatos(processo)
    todos_valor = _varrer_chaves_valor(processo)

    if resultado:
        caminho, bruto = resultado
        outros = [(c, v) for c, v in todos_valor if c != caminho]
        return {
            "cnj": cnj,
            "encontrado": True,
            "caminho": caminho,
            "valor_bruto": bruto,
            "valor": _parsear_valor_monetario(bruto),
            "candidatos_alternativos": outros,
        }

    if todos_valor:
        # Nenhum key-path mapeado bateu — usa o primeiro achado da varredura generica.
        caminho, bruto = todos_valor[0]
        return {
            "cnj": cnj,
            "encontrado": True,
            "caminho": caminho,
            "valor_bruto": bruto,
            "valor": _parsear_valor_monetario(bruto),
            "candidatos_alternativos": todos_valor[1:],
            "_aviso": "Valor obtido via varredura generica (key-path nao mapeado em CANDIDATOS_VALOR).",
        }

    return {
        "cnj": cnj,
        "encontrado": False,
        "caminho": None,
        "valor_bruto": None,
        "valor": None,
        "candidatos_alternativos": [],
        "_erro": "Nenhum campo de valor encontrado na capa deste processo.",
    }


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    cnj = sys.argv[1].strip() if len(sys.argv) > 1 else CNJ_ALVO

    print("=" * 60)
    print("PDPJ — Valor da Causa (via capa do processo)")
    print(f"CNJ: {cnj}")
    print("=" * 60)

    pdpj.ACCESS_TOKEN = pdpj.carregar_access_token()
    pdpj.HEADERS = {
        "Authorization": f"Bearer {pdpj.ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    print("\n[1/2] Buscando processo via /api/v2/processos/{cnj}...")
    resultado = buscar_valor_causa(cnj)

    print("\n[2/2] Resultado:")
    if resultado["encontrado"]:
        print(f"  Caminho    : {resultado['caminho']}")
        print(f"  Valor bruto: {resultado['valor_bruto']!r}")
        print(f"  Valor (R$) : {resultado['valor']}")
        if resultado.get("_aviso"):
            print(f"  [AVISO] {resultado['_aviso']}")
    else:
        print(f"  [NAO ENCONTRADO] {resultado.get('_erro', '')}")

    if resultado["candidatos_alternativos"]:
        print("\n  Outras chaves com 'valor' encontradas na capa (conferencia manual):")
        for caminho, bruto in resultado["candidatos_alternativos"][:20]:
            print(f"    {caminho} = {bruto!r}")

    os.makedirs(PASTA_SAIDA, exist_ok=True)
    cnj_safe = pdpj._nome_arquivo_seguro(cnj)
    caminho_saida = os.path.join(PASTA_SAIDA, f"{cnj_safe}.json")
    with open(caminho_saida, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"\nJSON salvo em: {caminho_saida}")


if __name__ == "__main__":
    main()
