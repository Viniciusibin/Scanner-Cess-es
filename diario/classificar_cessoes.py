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

# Truncagem: remove bloco de ADVs antes desse limite
MAX_CARACTERES_TEXTO = 10_000


# ============================================================
# CONFIGURACAO (.env)
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
# PRÉ-PROCESSAMENTO DO TEXTO
# ============================================================

def _preprocessar_texto(texto: str, max_chars: int = MAX_CARACTERES_TEXTO) -> str:
    """Remove o bloco de advogados no final da publicação (ruído para o LLM)
    e limita o tamanho do texto enviado."""
    corte = texto.find("- ADV:")
    if corte > 500:
        texto = texto[:corte]
    return texto[:max_chars]


# ============================================================
# PROMPT
# ============================================================

PROMPT_SISTEMA = (
    "Você é um assistente jurídico especializado em recuperação judicial e "
    "falência, trabalhando para um fundo de crédito distressed. "
    "Vai receber o texto de uma publicação do Diário de Justiça Eletrônico "
    "que já passou por filtro de palavras-chave. Sua tarefa é confirmar se "
    "descreve uma cessão de crédito CONCURSAL OU EXTRACONCURSAL relevante "
    "para um investidor em crédito distressed.\n\n"

    "MARQUE is_cessao_real=true APENAS se o texto descrever:\n"
    "- Compra ou aquisição de crédito concursal por FIDC, securitizadora, "
    "gestora de ativos ou fundo de investimento\n"
    "- Habilitação ou substituição processual por cessionário institucional "
    "em processo de RJ ou falência\n"
    "- Cessão fiduciária de recebíveis discutida no contexto de RJ/falência "
    "com valor relevante (acima de R$ 50.000)\n"
    "- Homologação judicial de cessão de crédito em massa falida\n"
    "- Notícia formal de cessão com identificação de cedente e cessionário "
    "institucional dentro de processo concursal\n\n"

    "MARQUE is_cessao_real=false SE:\n"
    "- Cessionário é pessoa física (ex.: Maria da Silva, João Ferreira)\n"
    "- É sub-rogação trabalhista: empresa pagou rescisão de funcionário e "
    "se habilita no lugar dele no QGC — padrão comum em TAM/LATAM, Azul, "
    "Gol, grandes empregadores em RJ\n"
    "- É cessão de precatório ou execução contra a Fazenda Pública (UPEFAZ, "
    "DEPRE, Fazenda Estadual) sem empresa privada em RJ/falência como "
    "devedora principal\n"
    "- O texto menciona cessão apenas como contexto histórico ou incidental, "
    "sem evento novo sendo comunicado nessa publicação\n"
    "- Cessão de cotas societárias, UPIs ou ativos operacionais (não crédito)\n"
    "- Tutela cautelar antecedente que apenas discute cessão fiduciária "
    "sem homologar ou confirmar transferência de crédito\n"
    "- Cessão de crédito fora de qualquer processo de RJ ou falência\n\n"

    "Para o campo tipo_cessao, use um dos seguintes valores:\n"
    "  'concursal'         — cessão de crédito sujeito à RJ/falência\n"
    "  'extraconcursal'    — cessão de crédito fora do concurso (ex.: "
    "fiduciária, DIP)\n"
    "  'sub-rogacao'       — empresa se sub-roga em direito de ex-funcionário\n"
    "  'precatorio_fazenda'— cessão de precatório contra ente público\n"
    "  'fiduciaria'        — cessão fiduciária de recebíveis\n"
    "  'outro'             — não se encaixa nos anteriores\n\n"

    "Para o campo cessionario_institucional, marque true se o cessionário "
    "for FIDC, fundo de investimento, securitizadora, gestora de ativos, "
    "banco ou empresa de factoring. Marque false se for pessoa física ou "
    "empresa não financeira comprando crédito de forma isolada.\n\n"

    "Responda SOMENTE com um JSON no formato abaixo — sem texto fora do JSON:\n"
    "{\n"
    '  "is_cessao_real": true ou false,\n'
    '  "tipo_cessao": "concursal" | "extraconcursal" | "sub-rogacao" | '
    '"precatorio_fazenda" | "fiduciaria" | "outro" | null,\n'
    '  "cessionario_institucional": true ou false,\n'
    '  "confianca": "alta" | "media" | "baixa",\n'
    '  "resumo": "1-2 frases resumindo o que foi identificado",\n'
    '  "cedente": "nome de quem cedeu o crédito, ou null",\n'
    '  "cessionario": "nome de quem adquiriu o crédito, ou null",\n'
    '  "valor": "valor do crédito cedido como aparece no texto, ou null",\n'
    '  "classe_credito": "quirografário | trabalhista | com garantia real | '
    'extraconcursal | outro | null",\n'
    '  "cnj_rj": "CNJ do processo de RJ/falência se diferente do CNJ da '
    'publicação, ou null",\n'
    '  "recuperanda": "nome da empresa em RJ/falência, ou null",\n'
    '  "motivo_classificacao": "explicação curta da decisão"\n'
    "}\n"
    "Não invente valores ausentes no texto — use null quando não encontrar."
)


# ============================================================
# FILTRO ESTRUTURAL PÓS-LLM
# ============================================================

# Órgãos que processam execuções contra a Fazenda — cessões aqui raramente
# são concursais relevantes para distressed
_ORGAOS_FAZENDA = frozenset({
    "upefaz",
    "fazenda pública",
    "fazenda publica",
    "depre",
    "execuções contra a fazenda",
    "execucoes contra a fazenda",
})

# Cessionários que indicam sub-rogação trabalhista, não compra de crédito
_CESSIONARIOS_SUBROGACAO = frozenset({
    "tam linhas aereas",
    "tam linhas aéreas",
    "latam airlines",
    "azul linhas aereas",
    "azul linhas aéreas",
    "gol linhas aereas",
    "gol linhas aéreas",
    "avianca brasil",
})


def _e_falso_positivo_estrutural(
    publicacao: dict, classificacao: dict
) -> tuple[bool, str]:
    """Regras determinísticas que sobrepõem o resultado do LLM.

    Retorna (True, motivo) quando o registro deve ser descartado mesmo que
    o LLM tenha marcado is_cessao_real=true.
    """
    orgao = (publicacao.get("orgao") or "").lower()
    cessionario = (classificacao.get("cessionario") or "").lower()
    tipo = (classificacao.get("tipo_cessao") or "").lower()
    institucional = classificacao.get("cessionario_institucional", False)

    # Execuções contra a Fazenda sem cessionário institucional
    if any(termo in orgao for termo in _ORGAOS_FAZENDA):
        if not institucional:
            return True, "UPEFAZ/Fazenda sem cessionário institucional"

    # Sub-rogação trabalhista por grandes empregadores conhecidos
    if any(nome in cessionario for nome in _CESSIONARIOS_SUBROGACAO):
        return True, f"Sub-rogação trabalhista — cessionário: {cessionario}"

    # O próprio LLM identificou como sub-rogação ou precatório contra Fazenda
    if tipo == "sub-rogacao":
        return True, "LLM classificou como sub-rogação trabalhista"
    if tipo == "precatorio_fazenda":
        return True, "LLM classificou como precatório contra Fazenda"

    return False, ""


# ============================================================
# RELEVÂNCIA BTG
# ============================================================

def _calcular_relevancia_btg(classificacao: dict) -> str:
    """Pontuação de relevância para o BTG com base nos campos extraídos pelo LLM.

    Retorna: 'alta' | 'media' | 'baixa' | 'irrelevante'
    """
    if not classificacao.get("is_cessao_real"):
        return "irrelevante"

    tipo = (classificacao.get("tipo_cessao") or "").lower()
    institucional = classificacao.get("cessionario_institucional", False)
    confianca = (classificacao.get("confianca") or "").lower()

    if tipo == "concursal" and institucional:
        return "alta" if confianca == "alta" else "media"

    if tipo == "extraconcursal" and institucional:
        return "media"

    if tipo == "fiduciaria":
        return "media"

    if tipo == "concursal" and not institucional:
        return "baixa"

    return "baixa"


# ============================================================
# CLASSIFICACAO
# ============================================================

def classificar_texto(texto: str, tentativas: int = 3) -> dict:
    """Chama o Azure GPT para classificar um único texto.
    Levanta a última exceção se todas as tentativas falharem."""
    texto_processado = _preprocessar_texto(texto)

    corpo = {
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": texto_processado},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    ultimo_erro: Exception | None = None
    for tentativa in range(tentativas):
        try:
            resp = requests.post(
                _montar_url(),
                headers=_montar_headers(),
                json=corpo,
                timeout=60,
            )
            # Alguns deployments mais antigos não aceitam response_format
            if resp.status_code == 400 and "response_format" in resp.text:
                corpo_sem_format = {k: v for k, v in corpo.items() if k != "response_format"}
                resp = requests.post(
                    _montar_url(),
                    headers=_montar_headers(),
                    json=corpo_sem_format,
                    timeout=60,
                )
            resp.raise_for_status()
            conteudo = resp.json()["choices"][0]["message"]["content"]
            return _extrair_json(conteudo)
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as exc:
            ultimo_erro = exc
            if tentativa < tentativas - 1:
                time.sleep(2 * (tentativa + 1))

    raise ultimo_erro  # type: ignore[misc]


def _montar_publicacao(match: dict, classificacao: dict, descoberto_em: str) -> dict:
    relevancia = _calcular_relevancia_btg(classificacao)
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
        "relevancia_btg": relevancia,
        "classificacoes": [
            {
                "is_cessao_real": bool(classificacao.get("is_cessao_real")),
                "tipo_cessao": classificacao.get("tipo_cessao"),
                "cessionario_institucional": bool(
                    classificacao.get("cessionario_institucional")
                ),
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


# ============================================================
# LOTE
# ============================================================

def classificar_lote(
    matches: list[dict],
    output_file: Path,
    cessoes_reais_file: Path,
    pausa: float = 0.5,
) -> None:
    """Classifica cada match via IA, aplica filtro estrutural e grava:
    - output_file        → todos os registros classificados
    - cessoes_reais_file → apenas os confirmados como cessão real relevante
    """
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
        except Exception as exc:  # noqa: BLE001
            print(f"[ERRO IA: {exc}]")
            continue

        publicacao = _montar_publicacao(match, classificacao, hoje)
        entrada = publicacao["classificacoes"][0]
        e_real = entrada["is_cessao_real"]

        # Filtro estrutural — sobrepõe o LLM quando necessário
        if e_real:
            falso, motivo = _e_falso_positivo_estrutural(publicacao, classificacao)
            if falso:
                entrada["is_cessao_real"] = False
                entrada["motivo_classificacao"] = (
                    (entrada.get("motivo_classificacao") or "") + f" [FILTRO: {motivo}]"
                )
                publicacao["relevancia_btg"] = "irrelevante"
                e_real = False

        classificados.append(publicacao)

        if e_real:
            reais.append(publicacao)
            rel = publicacao["relevancia_btg"]
            print(f"REAL ({entrada.get('confianca')}) — relevância={rel}")
        else:
            print("falso positivo")

        if i < total:
            time.sleep(pausa)

    # Grava arquivos de saída
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(classificados, f, ensure_ascii=False, indent=2)

    with open(cessoes_reais_file, "w", encoding="utf-8") as f:
        json.dump(reais, f, ensure_ascii=False, indent=2)

    reais_alta = sum(1 for r in reais if r.get("relevancia_btg") == "alta")
    reais_media = sum(1 for r in reais if r.get("relevancia_btg") == "media")

    print(
        f"\n{len(classificados)} classificado(s) | "
        f"{len(reais)} real(is) — "
        f"{reais_alta} alta relevância, {reais_media} média relevância."
    )