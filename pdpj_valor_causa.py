"""
pdpj_valor_causa.py — Busca o valor da causa de cada processo do database na PDPJ
===================================================================
Uso:
    python pdpj_valor_causa.py                          # processa database/TJ-MS-2026.json
    python pdpj_valor_causa.py <caminho_para_outro.json>  # processa outro arquivo do database
    python pdpj_valor_causa.py <CNJ>                      # modo teste: consulta 1 CNJ e so imprime

Estrategia (por CNJ):
    1. GET {PDPJ_API_BASE}/api/v2/processos/{cnj} → capa do processo.
    2. Tenta um conjunto de key-paths conhecidos (raiz e tramitacaoAtual) onde o
       valor da causa costuma aparecer nos sistemas agregados pela PDPJ (PJe, eproc,
       Projudi, SAJ...) — o campo NAO e padronizado entre tribunais/graus.
    3. Fallback: varre recursivamente todo o JSON da capa procurando qualquer chave
       que contenha "valor" e usa o primeiro achado, guardando os demais como
       candidatos alternativos para conferencia manual.
    4. Normaliza o valor (ex.: "R$ 1.234.567,89" → 1234567.89).

Importante:
    Nem todo processo tem esse campo preenchido na capa — muitos sistemas de
    origem nao repassam "valor da causa" na integracao com a PDPJ. Quando isso
    acontecer, os campos "valor_causa" ficam None e "valor_causa_erro" explica o motivo.

Efeito no database:
    Cada item do JSON processado recebe o campo "valor_causa" (float ou None).
    O arquivo e sobrescrito no mesmo caminho.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PDPJ_API_BASE = "https://portaldeservicos.pdpj.jus.br"
TOKEN_PATH = os.path.join(_SCRIPT_DIR, "token.json")

DATABASE_PADRAO = os.path.join(_SCRIPT_DIR, "database", "TJ-MS-2026.json")

# Key-paths conhecidos onde o valor da causa costuma aparecer na capa da PDPJ.
# "raiz" = nivel raiz do objeto de processo; "tramitacaoAtual" = sub-objeto de tramitacao.
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
# AUTENTICACAO / HTTP PDPJ
# ============================================================

def carregar_access_token() -> str:
    """Le o access_token salvo em token.json (gerado pelo login na PDPJ)."""
    with open(TOKEN_PATH, encoding="utf-8") as f:
        token = json.load(f)
    return token["access_token"]


def _montar_headers() -> dict:
    return {
        "Authorization": f"Bearer {carregar_access_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def buscar_processo_completo(cnj: str) -> dict | None:
    """GET /api/v2/processos/{cnj} na PDPJ. Retorna a capa do processo ou None."""
    url = f"{PDPJ_API_BASE}/api/v2/processos/{cnj}"
    resp = requests.get(url, headers=_montar_headers(), timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    dados = resp.json()
    if isinstance(dados, list):
        return dados[0] if dados else None
    return dados


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
        # So virgula: assume separador decimal BR -> "1234567,89" -> "1234567.89"
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


def buscar_valor_causa(cnj: str) -> dict:
    """
    Busca o valor da causa de um processo pelo CNJ, a partir da capa (JSON de metadados).

    Retorna dict com:
        cnj, encontrado, caminho (key-path onde achou), valor_bruto, valor (float),
        candidatos_alternativos (outras chaves "valor*" achadas, para conferencia).
    """
    try:
        processo = buscar_processo_completo(cnj)
    except requests.RequestException as exc:
        return {
            "cnj": cnj,
            "encontrado": False,
            "caminho": None,
            "valor_bruto": None,
            "valor": None,
            "candidatos_alternativos": [],
            "_erro": f"Falha na requisicao a PDPJ: {exc}",
        }

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
# PROCESSAMENTO DO DATABASE (lista de processos com campo "cnj")
# ============================================================

def processar_database(caminho_json: str, pausa_seg: float = 0.3) -> None:
    """Le um JSON do database, consulta o valor da causa de cada "cnj" e regrava o arquivo."""
    with open(caminho_json, encoding="utf-8") as f:
        itens = json.load(f)

    total = len(itens)
    for i, item in enumerate(itens, start=1):
        cnj = item.get("cnj")
        if not cnj:
            continue

        print(f"[{i}/{total}] {cnj} ...", end=" ", flush=True)
        resultado = buscar_valor_causa(cnj)

        item["valor_causa"] = resultado["valor"]

        if resultado["encontrado"]:
            print(f"OK — {resultado['valor']}")
        else:
            print(f"NAO ENCONTRADO — {resultado.get('_erro', '')}")

        if i < total:
            time.sleep(pausa_seg)

    with open(caminho_json, "w", encoding="utf-8") as f:
        json.dump(itens, f, ensure_ascii=False, indent=2)
    print(f"\nDatabase atualizado: {caminho_json}")


# ============================================================
# MAIN
# ============================================================

_CNJ_REGEX = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


def main() -> None:
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else None

    if arg and _CNJ_REGEX.match(arg):
        # Modo teste: consulta um unico CNJ e apenas imprime (nao grava no database).
        print("=" * 60)
        print("PDPJ — Valor da Causa (modo teste, 1 CNJ)")
        print(f"CNJ: {arg}")
        print("=" * 60)
        resultado = buscar_valor_causa(arg)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return

    caminho_json = arg or DATABASE_PADRAO
    print("=" * 60)
    print("PDPJ — Valor da Causa (via capa do processo)")
    print(f"Database: {caminho_json}")
    print("=" * 60)
    processar_database(caminho_json)


if __name__ == "__main__":
    main()
