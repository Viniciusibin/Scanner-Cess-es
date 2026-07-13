"""
Classificação por IA (Azure GPT) dos matches encontrados por scan_cessoes.py.

Para cada match, pergunta ao modelo se o texto da publicação descreve uma
cessão de crédito REAL dentro do contexto de recuperação judicial/falência
(ex.: substituição de credor no quadro geral de credores, habilitação por
cessionário, notícia de cessão/aquisição de crédito extraconcursal) e extrai
os campos usados no resto do pipeline (cedente, cessionário, valor, etc.).

Não pede a data da cessão (isso é responsabilidade de outro script, fora
desta pipeline).

Uso:
    from classificar_cessoes import classificar_lote
    classificar_lote(matches, output_file, cessoes_reais_file)
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_ENV_PATH = _REPO_ROOT / ".env"

MAX_CARACTERES_TEXTO = 12000


# ============================================================
# CONFIGURACAO (.env) — mesmo padrão de data_cessao_credito.py
# ============================================================

def _carregar_env(caminho: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    if not caminho.exists():
        return valores

    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, resto = linha.partition("=")
            valor = resto.split("#", 1)[0].strip().strip('"').strip("'")
            valores[chave.strip()] = valor
    return valores


_ENV = _carregar_env(_ENV_PATH)


def _config(nome: str) -> str:
    valor = os.getenv(nome) or _ENV.get(nome)
    if not valor:
        sys.exit(f"Configuracao ausente: defina {nome} no .env ou como variavel de ambiente.")
    return valor


def _montar_url() -> str:
    api_base = _config("GPT_API_BASE")
    modelo = _config("GPT_MODEL")
    versao = _config("GPT_API_VERSION")
    return f"{api_base}/{modelo}/chat/completions?api-version={versao}"


def _montar_headers() -> dict[str, str]:
    headers = {"api-key": _config("API_GPT"), "Content-Type": "application/json"}
    projeto = os.getenv("GPT_PROJECT") or _ENV.get("GPT_PROJECT", "")
    if projeto:
        headers["X-Project"] = projeto
    return headers


def _extrair_json(conteudo: str) -> dict:
    try:
        return json.loads(conteudo)
    except json.JSONDecodeError:
        inicio, fim = conteudo.find("{"), conteudo.rfind("}")
        if inicio == -1 or fim == -1:
            raise
        return json.loads(conteudo[inicio : fim + 1])


# ============================================================
# CLASSIFICACAO
# ============================================================

PROMPT_SISTEMA = (
    "Você é um assistente jurídico especializado em recuperação judicial e "
    "falência. Vai receber o texto de uma publicação do Diário de Justiça "
    "Eletrônico que já passou por um filtro de palavras-chave sugerindo uma "
    "possível cessão de crédito dentro de um processo de recuperação "
    "judicial/falência. Sua tarefa é confirmar se é REALMENTE uma cessão de "
    "crédito (substituição de credor, habilitação de crédito por "
    "cessionário, notícia de cessão/aquisição de crédito extraconcursal "
    "etc.) ou se é um falso positivo (ex.: menção incidental, cessão de "
    "cotas societárias, termo usado em outro sentido).\n\n"
    "Responda SOMENTE com um JSON no formato:\n"
    "{\n"
    '  "is_cessao_real": true ou false,\n'
    '  "confianca": "alta", "media" ou "baixa",\n'
    '  "resumo": "1-2 frases resumindo o que foi identificado",\n'
    '  "cedente": "nome de quem cedeu o crédito, ou null",\n'
    '  "cessionario": "nome de quem adquiriu o crédito, ou null",\n'
    '  "valor": "valor do crédito cedido, como aparece no texto, ou null",\n'
    '  "classe_credito": "classe do crédito na RJ (ex.: quirografário, '
    'trabalhista, com garantia real), ou null",\n'
    '  "cnj_rj": "CNJ do processo de recuperação judicial/falência, se '
    'diferente do CNJ da publicação, ou null",\n'
    '  "recuperanda": "nome da empresa em recuperação judicial/falência, ou '
    'null",\n'
    '  "motivo_classificacao": "explicação curta da decisão"\n'
    "}\n"
    "Não invente valores que não estejam no texto — use null quando não "
    "encontrar. Se is_cessao_real for false, ainda assim preencha resumo e "
    "motivo_classificacao explicando por que foi descartado."
)


def classificar_texto(texto: str, tentativas: int = 3) -> dict:
    """Chama o Azure GPT para classificar um único texto. Levanta a última
    exceção se todas as tentativas falharem."""
    corpo = {
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": texto[:MAX_CARACTERES_TEXTO]},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    ultimo_erro: Exception | None = None
    for tentativa in range(tentativas):
        try:
            resp = requests.post(_montar_url(), headers=_montar_headers(), json=corpo, timeout=60)
            if resp.status_code == 400 and "response_format" in resp.text:
                corpo_sem_format = {k: v for k, v in corpo.items() if k != "response_format"}
                resp = requests.post(_montar_url(), headers=_montar_headers(), json=corpo_sem_format, timeout=60)
            resp.raise_for_status()
            conteudo = resp.json()["choices"][0]["message"]["content"]
            return _extrair_json(conteudo)
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as exc:
            ultimo_erro = exc
            if tentativa < tentativas - 1:
                time.sleep(2 * (tentativa + 1))

    raise ultimo_erro  # type: ignore[misc]


def _montar_publicacao(match: dict, classificacao: dict, descoberto_em: str) -> dict:
    return {
        "id": match.get("id"),
        "cnj": match.get("cnj"),
        "classe": match.get("classe"),
        "orgao": match.get("orgao"),
        "destinatarios": match.get("destinatarios", []),
        "link": match.get("link"),
        "texto_completo": match.get("texto_completo"),
        "data": match.get("data"),
        "valor_causa": None,
        "keywords_cessao_fortes": match.get("keywords_cessao_fortes", []),
        "keywords_cessao_fracas": match.get("keywords_cessao_fracas", []),
        "keywords_rj": match.get("keywords_rj", []),
        "arquivo_origem": match.get("arquivo_origem"),
        "classificacoes": [
            {
                "is_cessao_real": bool(classificacao.get("is_cessao_real")),
                "confianca": classificacao.get("confianca"),
                "resumo": classificacao.get("resumo"),
                "cedente": classificacao.get("cedente"),
                "cessionario": classificacao.get("cessionario"),
                "valor": classificacao.get("valor"),
                "classe_credito": classificacao.get("classe_credito"),
                "cnj_rj": classificacao.get("cnj_rj"),
                "recuperanda": classificacao.get("recuperanda"),
                "motivo_classificacao": classificacao.get("motivo_classificacao"),
                "descoberto_em": descoberto_em,
            }
        ],
    }


def classificar_lote(matches: list[dict], output_file: Path, cessoes_reais_file: Path, pausa: float = 0.5) -> None:
    """Classifica cada match via IA e grava output_file (todos) e
    cessoes_reais_file (só os com is_cessao_real=true)."""
    hoje = date.today().isoformat()
    classificados: list[dict] = []
    reais: list[dict] = []

    total = len(matches)
    for i, match in enumerate(matches, start=1):
        texto = match.get("texto_completo") or ""
        print(f"[{i}/{total}] {match.get('cnj', '?')} ...", end=" ", flush=True)

        if not texto:
            print("sem texto, pulado")
            continue

        try:
            classificacao = classificar_texto(texto)
        except Exception as exc:  # noqa: BLE001 - reportar e seguir para o próximo match
            print(f"[ERRO IA: {exc}]")
            continue

        publicacao = _montar_publicacao(match, classificacao, hoje)
        classificados.append(publicacao)
        if publicacao["classificacoes"][0]["is_cessao_real"]:
            reais.append(publicacao)
            print(f"REAL ({classificacao.get('confianca')})")
        else:
            print("falso positivo")

        if i < total:
            time.sleep(pausa)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(classificados, f, ensure_ascii=False, indent=2)

    with open(cessoes_reais_file, "w", encoding="utf-8") as f:
        json.dump(reais, f, ensure_ascii=False, indent=2)

    print(f"\n{len(classificados)} classificado(s), {len(reais)} cessão(ões) real(is) confirmada(s).")
