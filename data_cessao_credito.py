"""
data_cessao_credito.py — Busca a data da cessão de crédito de cada processo via IA (Azure GPT)
================================================================================
Uso:
    python data_cessao_credito.py                          # processa todos os JSONs em database/
    python data_cessao_credito.py <caminho_para_outro.json>  # processa outro arquivo do database
    python data_cessao_credito.py <CNJ>                      # modo teste: consulta 1 CNJ e so imprime

Estrategia:
    1. Para cada item do database (documento com "texto_completo"), pergunta ao
       modelo GPT (via Azure, configurado no .env) se o texto menciona
       EXPLICITAMENTE a data em que a cessao de credito ocorreu (data do
       contrato/instrumento de cessao, da notificacao ao devedor, ou da
       comunicacao/pedido de habilitacao/substituicao de credor no juizo).
    2. Fallback: quando o texto nao menciona a data da cessao, consulta a API
       publica do DJEN (comunicaapi.pje.jus.br) pelo campo "id" do item — que
       e o numero da comunicacao — e usa a "data_publicacao" do documento
       (data em que a publicacao foi disponibilizada no Diario de Justica).

Efeito no database:
    Cada item do JSON processado recebe o campo "data_cessao_credito" (string),
    direto no proprio JSON analisado — mesmo padrao do "valor_causa" gerado por
    pdpj_valor_causa.py:
        - a data encontrada no texto (como o modelo a formatar); ou
        - a data de publicacao do documento no DJEN, quando o texto nao
          menciona a data da cessao; ou
        - "Nao encontrado" apenas se nenhuma das duas fontes acima funcionar
          (ex.: sem texto, sem "id", ou falha ao consultar o DJEN).
    O arquivo e sobrescrito no mesmo caminho.

    Itens que ja tem o campo sao pulados por padrao (script retomavel) —
    use --forcar para reprocessar tudo.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_SCRIPT_DIR, ".env")
DATABASE_DIR = os.path.join(_SCRIPT_DIR, "database")

CAMPO_DATA = "data_cessao_credito"
NAO_ENCONTRADO = "Não encontrado"

MAX_CARACTERES_TEXTO = 12000  # limite de contexto enviado ao modelo por documento


# ============================================================
# CONFIGURACAO (.env)
# ============================================================

def _carregar_env(caminho: str) -> dict[str, str]:
    """Parser minimo para o .env do projeto (formato `CHAVE = "valor"  # comentario`)."""
    valores: dict[str, str] = {}
    if not os.path.exists(caminho):
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


API_GPT = _config("API_GPT")
GPT_MODEL = _config("GPT_MODEL")
GPT_API_BASE = _config("GPT_API_BASE")
GPT_API_VERSION = _config("GPT_API_VERSION")
GPT_PROJECT = os.getenv("GPT_PROJECT") or _ENV.get("GPT_PROJECT", "")

DJEN_API_BASE = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"


# ============================================================
# FALLBACK: DATA DE PUBLICACAO NO DJEN
# ============================================================

def buscar_data_publicacao_djen(comunicacao_id: object, tentativas: int = 3) -> str | None:
    """Consulta a API publica do DJEN pelo id da comunicacao (campo "id" do item)
    e retorna a data de publicacao do documento no formato DD/MM/AAAA, ou None
    se o id estiver ausente, a comunicacao nao existir, ou a consulta falhar.

    A API publica do DJEN e instavel sob uso continuo (erros 500 esporadicos),
    entao tenta algumas vezes com um pequeno intervalo antes de desistir."""
    if not comunicacao_id:
        return None

    for tentativa in range(tentativas):
        try:
            resp = requests.get(f"{DJEN_API_BASE}/{comunicacao_id}", timeout=20)
            if resp.status_code >= 500:
                raise requests.HTTPError(f"DJEN respondeu {resp.status_code}")
            resp.raise_for_status()
            itens = resp.json().get("items") or []
            if not itens:
                return None
            data_publicacao = itens[0].get("data_publicacao")
            if not data_publicacao:
                return None
            ano, mes, dia = data_publicacao.split("-")
            return f"{dia}/{mes}/{ano}"
        except (requests.RequestException, json.JSONDecodeError, ValueError, KeyError, IndexError):
            if tentativa < tentativas - 1:
                time.sleep(1.5)
                continue
            return None


# ============================================================
# CHAMADA AO MODELO
# ============================================================

PROMPT_SISTEMA = (
    "Voce e um assistente juridico especializado em processos de recuperacao "
    "judicial. Sua tarefa e ler o texto de uma publicacao/decisao judicial e "
    "identificar se ele menciona EXPLICITAMENTE a data em que uma cessao de "
    "credito ocorreu (data do contrato/instrumento de cessao, data da "
    "notificacao ao devedor, ou data da comunicacao/pedido de habilitacao/"
    "substituicao de credor no juizo). Nao invente nem infira datas que nao "
    "estejam escritas no texto — nesse caso, marque como nao encontrado.\n\n"
    "Responda SOMENTE com um JSON no formato:\n"
    '{"encontrado": true, "data_cessao": "texto da data como aparece no documento"}\n'
    'Quando o texto nao mencionar nenhuma data de cessao, use '
    '"encontrado": false e "data_cessao": null.'
)


def _montar_url() -> str:
    return f"{GPT_API_BASE}/{GPT_MODEL}/chat/completions?api-version={GPT_API_VERSION}"


def _montar_headers() -> dict[str, str]:
    headers = {"api-key": API_GPT, "Content-Type": "application/json"}
    if GPT_PROJECT:
        headers["X-Project"] = GPT_PROJECT
    return headers


def _extrair_json(conteudo: str) -> dict:
    """Tenta json.loads direto; se vier com texto ao redor, recorta entre a 1a { e a ultima }."""
    try:
        return json.loads(conteudo)
    except json.JSONDecodeError:
        inicio, fim = conteudo.find("{"), conteudo.rfind("}")
        if inicio == -1 or fim == -1:
            raise
        return json.loads(conteudo[inicio : fim + 1])


def buscar_data_cessao(texto_completo: str) -> dict:
    """Pergunta ao modelo se o texto menciona a data da cessao de credito."""
    corpo = {
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": texto_completo[:MAX_CARACTERES_TEXTO]},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(_montar_url(), headers=_montar_headers(), json=corpo, timeout=60)
    if resp.status_code == 400 and "response_format" in resp.text:
        # fallback: proxy/modelo pode nao suportar response_format
        corpo.pop("response_format")
        resp = requests.post(_montar_url(), headers=_montar_headers(), json=corpo, timeout=60)
    resp.raise_for_status()

    conteudo = resp.json()["choices"][0]["message"]["content"]
    return _extrair_json(conteudo)


# ============================================================
# PROCESSAMENTO DO DATABASE (lista de itens com campo "texto_completo")
# ============================================================

_MESES = (
    "janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|"
    "setembro|outubro|novembro|dezembro"
)
_PADRAO_DATA = re.compile(
    rf"\d{{1,2}}\s*[/.\-]\s*\d{{1,2}}\s*[/.\-]\s*\d{{2,4}}"       # 18/02/2025, 11.7.2014
    rf"|\d{{1,2}}\s+de\s+(?:{_MESES})\s+de\s+\d{{4}}"             # 18 de fevereiro de 2025
    rf"|\b(?:{_MESES})\s+de\s+\d{{4}}\b",                         # março de 2017
    re.IGNORECASE,
)


def _extrair_data(valor: str) -> str | None:
    """Extrai so o trecho de data de dentro da resposta da IA, descartando
    qualquer texto ao redor (ex.: "instrumento contratual celebrado em
    1.4.2021" -> "1.4.2021"). Retorna None se nao achar nenhuma data valida
    ou se o valor for longo demais para ser uma resposta confiavel."""
    if len(valor) > 200:
        return None
    encontrado = _PADRAO_DATA.search(valor)
    return encontrado.group(0) if encontrado else None


def _campo_valido(item: dict) -> bool:
    """Um valor ja gravado e valido (extracao limpa) se for NAO_ENCONTRADO
    ou o proprio texto de uma data (sem sobra de frase ao redor)."""
    valor = item.get(CAMPO_DATA)
    if valor is None:
        return False
    valor = str(valor)
    return valor == NAO_ENCONTRADO or _extrair_data(valor) == valor


def processar_item(item: dict, forcar: bool) -> bool:
    """Preenche/corrige data_cessao_credito no proprio item.

    Um item so e pulado quando ja tem uma data valida e o chamador nao pediu
    --forcar. Itens marcados como NAO_ENCONTRADO tentam de novo apenas o
    fallback do DJEN (barato, sem custo de IA) a cada execucao — util porque
    a API publica do DJEN e instavel e pode ter falhado numa tentativa
    anterior. Retorna True se alguma tentativa foi feita nesta chamada.
    """
    valor_atual = item.get(CAMPO_DATA)
    ja_tem_data_valida = valor_atual is not None and valor_atual != NAO_ENCONTRADO and _campo_valido(item)

    if ja_tem_data_valida and not forcar:
        return False

    precisa_tentar_ia = forcar or valor_atual is None or not _campo_valido(item)

    data_extraida = None
    if precisa_tentar_ia:
        texto = item.get("texto_completo") or ""
        if texto:
            try:
                resultado = buscar_data_cessao(texto)
                if resultado.get("encontrado") and resultado.get("data_cessao"):
                    data_extraida = _extrair_data(str(resultado["data_cessao"]).strip())
            except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as exc:
                print(f"[ERRO IA: {exc}]", end=" ")

    if data_extraida:
        item[CAMPO_DATA] = data_extraida
    else:
        # Texto nao ajudou (ou ja sabiamos que nao ajudaria) — tenta o DJEN.
        item[CAMPO_DATA] = buscar_data_publicacao_djen(item.get("id")) or NAO_ENCONTRADO

    return True


def processar_arquivo(caminho: str, forcar: bool, pausa: float) -> None:
    """Le um JSON do database, busca a data de cessao de cada item e regrava o arquivo."""
    with open(caminho, encoding="utf-8") as f:
        itens = json.load(f)

    total = len(itens)
    alterados = 0
    for i, item in enumerate(itens, start=1):
        cnj = item.get("cnj", "?")
        print(f"[{i}/{total}] {cnj} ...", end=" ", flush=True)

        mudou = processar_item(item, forcar=forcar)
        if mudou:
            alterados += 1
            print(item[CAMPO_DATA])
            time.sleep(pausa)
        else:
            print("ja processado, pulado")

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(itens, f, ensure_ascii=False, indent=2)

    print(f"Arquivo atualizado: {caminho} ({alterados}/{total} itens alterados)\n")


# ============================================================
# MAIN
# ============================================================

_CNJ_REGEX = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


def _encontrar_item_por_cnj(cnj: str) -> tuple[str, dict] | None:
    """Modo teste: procura o CNJ em todos os JSONs de database/ (nao grava nada)."""
    for caminho in sorted(glob.glob(os.path.join(DATABASE_DIR, "*.json"))):
        with open(caminho, encoding="utf-8") as f:
            itens = json.load(f)
        for item in itens:
            if item.get("cnj") == cnj:
                return caminho, item
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrai a data da cessao de credito de cada item do database usando IA."
    )
    parser.add_argument(
        "alvo", nargs="?",
        help="JSON especifico a processar, ou um CNJ para modo teste; se omitido, processa todos em database/",
    )
    parser.add_argument(
        "--forcar", action="store_true",
        help="Reprocessa mesmo itens que ja tem data_cessao_credito",
    )
    parser.add_argument(
        "--pausa", type=float, default=0.5,
        help="Segundos de espera entre chamadas a IA (default: 0.5)",
    )
    args = parser.parse_args()

    if args.alvo and _CNJ_REGEX.match(args.alvo):
        # Modo teste: consulta um unico CNJ e apenas imprime (nao grava no database).
        encontrado = _encontrar_item_por_cnj(args.alvo)
        print("=" * 60)
        print("Data da Cessao de Credito (modo teste, 1 CNJ)")
        print(f"CNJ: {args.alvo}")
        print("=" * 60)
        if not encontrado:
            print("CNJ nao encontrado em nenhum JSON de database/.")
            return
        caminho, item = encontrado
        resultado = buscar_data_cessao(item.get("texto_completo") or "")
        print(f"Arquivo: {caminho}")
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return

    arquivos = [args.alvo] if args.alvo else sorted(glob.glob(os.path.join(DATABASE_DIR, "*.json")))
    if not arquivos:
        sys.exit(f"Nenhum JSON encontrado em {DATABASE_DIR}")

    print("=" * 60)
    print("Data da Cessao de Credito (via IA)")
    print(f"Arquivos: {len(arquivos)}")
    print("=" * 60)

    for caminho in arquivos:
        print(f"\n--- {caminho} ---")
        processar_arquivo(caminho, args.forcar, args.pausa)


if __name__ == "__main__":
    main()
