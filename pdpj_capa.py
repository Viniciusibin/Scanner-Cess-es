"""
pdpj_capa.py — Descobre a recuperanda pela capa dos autos na PDPJ
===================================================================
Uso:
    python pdpj_capa.py                     # busca os pendentes na PDPJ e ja mescla no database
    python pdpj_capa.py --merge              # so mescla (usa o que ja tem em recuperandas_capa/, nao chama a PDPJ)
    python pdpj_capa.py <CNJ>                # modo teste: consulta 1 CNJ e so imprime (nao grava nada)

Contexto:
    Em varias classificacoes de cessao de credito, o campo "recuperanda" ficou
    vazio (a IA nao conseguiu extrair o nome da recuperanda do texto da publicacao).

Estrategia:
    1. Varre database/*.json procurando classificacoes com is_cessao_real=True
       e "recuperanda" vazio/nulo.
    2. Para cada CNJ pendente, busca a capa do processo na PDPJ
       (GET /api/v2/processos/{cnj}) e le tramitacaoAtual.partes.
    3. Agrupa as partes por polo (ATIVO/PASSIVO/...) na ordem em que aparecem
       na capa e monta a legenda tal como o processo e nomeado nos autos, ex.:
       "RG ESTALEIRO ERG3 INDUSTRIAL S.A. e outros (5) X OS MESMOS".
    4. Salva o resultado em um json PROPRIO por CNJ, dentro da pasta
       recuperandas_capa/ (ex.: recuperandas_capa/5000021-98.2016.8.21.0023.json).
    5. Mescla de volta: para cada classificacao com "recuperanda" vazio no
       database/TJ-*.json, preenche com o nome do polo ATIVO encontrado no
       passo 4. Se houver mais de um nome no polo ativo, prioriza nessa ordem:
       variacao de S/A (S A, S/A, S.A) > LTDA > variacao de ME (ME, M E, M.E);
       se nenhum nome bater com esses padroes, usa o primeiro da lista.
       Este passo ALTERA os arquivos database/TJ-*.json.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import sys
import time
import unicodedata

import requests

# ============================================================
# CONFIGURACAO
# ============================================================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

TOKEN_FILE = os.path.join(_SCRIPT_DIR, "token.json")
ACCESS_TOKEN = ""
HEADERS: dict[str, str] = {}

BASE_URL = "https://portaldeservicos.pdpj.jus.br"
SSO_TOKEN_URL = "https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/token"
SSO_CLIENT_ID = "portalexterno-frontend"

PASTA_DATABASE = os.path.join(_SCRIPT_DIR, "database")
PASTA_SAIDA = os.path.join(_SCRIPT_DIR, "recuperandas_capa")

_CNJ_REGEX = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


# ============================================================
# TOKEN
# ============================================================

def _token_expirado(token_jwt: str, margem_seg: int = 60) -> bool:
    try:
        partes = token_jwt.split(".")
        payload = partes[1] + "=" * (-len(partes[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return time.time() >= data.get("exp", 0) - margem_seg
    except Exception:
        return True


def _renovar_token(refresh_token: str) -> dict | None:
    try:
        resp = requests.post(
            SSO_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": SSO_CLIENT_ID,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        print(f"  [DIAG] Erro ao renovar token: {exc}")
    return None


def _pedir_token_no_terminal() -> str:
    print(
        f"\nPara obter o token acesse {BASE_URL}, faca login,"
        "\nabra DevTools > Network, filtre por 'token' e copie o access_token."
        "\n\nCole o access_token e pressione Enter:"
    )
    access_token = input().strip()
    if not access_token:
        raise SystemExit("[ERRO] Nenhum access_token informado.")

    print("Cole o refresh_token e pressione Enter (ou Enter para pular):")
    refresh_token = input().strip()

    dados: dict = {}
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            dados = {}
    dados["access_token"] = access_token
    if refresh_token:
        dados["refresh_token"] = refresh_token
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    print("[TOKEN] Salvo em token.json")
    return access_token


def carregar_access_token() -> str:
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            dados = json.load(f)

        refresh_token = dados.get("refresh_token", "")
        if refresh_token and not _token_expirado(refresh_token):
            print("[TOKEN] Renovando via refresh_token...")
            novos = _renovar_token(refresh_token)
            if novos and novos.get("access_token"):
                dados.update(novos)
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    json.dump(dados, f, ensure_ascii=False, indent=2)
                print("[TOKEN] Token renovado.")
                return novos["access_token"]

        access_token = dados.get("access_token", "")
        if access_token and not _token_expirado(access_token):
            print("[TOKEN] Usando access_token existente.")
            return access_token

    return _pedir_token_no_terminal()


# ============================================================
# DATABASE — coleta de CNJs sem "recuperanda"
# ============================================================

def _normalizar(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", texto or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .upper()
        .strip()
    )


def _campo_vazio(valor: object) -> bool:
    return valor is None or not str(valor).strip()


def _nome_arquivo_seguro(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^\w\-.]", "_", texto)


def coletar_cnjs_sem_recuperanda(pasta_database: str = PASTA_DATABASE) -> dict[str, list[str]]:
    """Varre database/*.json e retorna {cnj: [arquivos TJ onde aparece]} para os
    CNJs cujas classificacoes de cessao real nao tem o campo 'recuperanda' preenchido."""
    fontes: dict[str, set[str]] = {}
    for caminho in sorted(glob.glob(os.path.join(pasta_database, "*.json"))):
        nome_arquivo = os.path.basename(caminho)
        with open(caminho, encoding="utf-8") as f:
            itens = json.load(f)
        for item in itens:
            for classificacao in item.get("classificacoes") or []:
                if not classificacao.get("is_cessao_real"):
                    continue
                if not _campo_vazio(classificacao.get("recuperanda")):
                    continue
                cnj = (classificacao.get("cnj_rj") or item.get("cnj") or "").strip()
                if cnj:
                    fontes.setdefault(cnj, set()).add(nome_arquivo)
    return {cnj: sorted(arquivos) for cnj, arquivos in sorted(fontes.items())}


# ============================================================
# API PDPJ — busca a capa do processo
# ============================================================

def _get(url: str, timeout: int = 30) -> requests.Response | None:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        print(f"    [ERRO] {url} -> {exc}")
        return None


def buscar_processo(cnj: str, tentativas: int = 4, espera_base: int = 10) -> dict | None:
    """GET /api/v2/processos/{cnj} — retorna o objeto de processo ou None.
    Faz retry automatico em respostas vazias ou erros 5xx."""
    url = f"{BASE_URL}/api/v2/processos/{cnj}"
    for tentativa in range(1, tentativas + 1):
        resp = _get(url)
        if not resp:
            espera = espera_base * tentativa
            print(f"    [RETRY {tentativa}/{tentativas}] Sem resposta — aguardando {espera}s...")
            time.sleep(espera)
            continue

        if resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = None

            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict) and data:
                return data

            espera = espera_base * tentativa
            print(f"    [VAZIO] HTTP 200 com dados vazios (tentativa {tentativa}/{tentativas}) — aguardando {espera}s...")
            time.sleep(espera)
            continue

        if resp.status_code in (502, 503, 504) and tentativa < tentativas:
            espera = espera_base * tentativa
            print(f"    [RETRY {tentativa}/{tentativas}] HTTP {resp.status_code} — aguardando {espera}s...")
            time.sleep(espera)
            continue

        print(f"    [HTTP {resp.status_code}] Processo nao encontrado ou sem acesso.")
        return None

    print(f"    [FALHA] Todas as {tentativas} tentativas falharam.")
    return None


def _extrair_partes_da_capa(processo: dict) -> list[dict]:
    """Localiza a lista de partes na capa (tramitacaoAtual.partes e alternativas)."""
    tramitacao_atual = processo.get("tramitacaoAtual") or {}
    tramitacoes = processo.get("tramitacoes") or []

    candidatos = [
        tramitacao_atual.get("partes"),
        *[t.get("partes") for t in tramitacoes if isinstance(t, dict)],
        processo.get("partes"),
        processo.get("polo"),
        (processo.get("dadosBasicos") or {}).get("partes"),
        processo.get("polos"),
    ]
    for candidato in candidatos:
        if isinstance(candidato, list) and candidato:
            return candidato
    return []


def montar_legenda_capa(partes_brutas: list[dict]) -> tuple[str, dict[str, list[str]]]:
    """
    Agrupa as partes por polo (na ordem em que aparecem na capa) e monta a
    legenda do processo, ex.: "FULANO S.A. e outros (5) X OS MESMOS".
    Retorna (legenda, {polo: [nomes...]}).
    """
    grupos: dict[str, list[str]] = {}
    for parte in partes_brutas:
        if not isinstance(parte, dict):
            continue
        nome = (parte.get("nome") or parte.get("nomeCompleto") or parte.get("razaoSocial") or "").strip()
        if not nome:
            continue
        polo = _normalizar(str(parte.get("polo") or parte.get("tipoParte") or "OUTROS"))
        nomes = grupos.setdefault(polo, [])
        if nome not in nomes:
            nomes.append(nome)

    rotulos = []
    for nomes in grupos.values():
        rotulo = nomes[0]
        if len(nomes) > 1:
            rotulo += f" e outros ({len(nomes) - 1})"
        rotulos.append(rotulo)

    return " X ".join(rotulos), grupos


def processar_cnj_capa(cnj: str, fontes_database: list[str] | None = None) -> dict:
    """Busca a capa do processo na PDPJ e monta o resultado para um CNJ."""
    base = {"cnj": cnj, "_fontes_database": fontes_database or []}

    processo = buscar_processo(cnj)
    if not processo:
        return {
            **base,
            "status": "erro",
            "erro": "Processo nao encontrado na PDPJ (capa).",
        }

    tramitacao_atual = processo.get("tramitacaoAtual") or {}
    classe = tramitacao_atual.get("classe") or []
    classe_descricao = classe[0].get("descricao") if classe and isinstance(classe[0], dict) else None

    partes_brutas = _extrair_partes_da_capa(processo)
    if not partes_brutas:
        return {
            **base,
            "status": "vazio",
            "erro": "Nenhuma parte encontrada na capa deste processo.",
            "classe": classe_descricao,
            "tribunal": processo.get("siglaTribunal"),
        }

    legenda, grupos = montar_legenda_capa(partes_brutas)
    return {
        **base,
        "status": "ok",
        "recuperanda_capa": legenda,
        "polos": grupos,
        "classe": classe_descricao,
        "tribunal": processo.get("siglaTribunal"),
    }


# ============================================================
# ARQUIVO DE SAIDA — um json por CNJ em PASTA_SAIDA/
# ============================================================

def _caminho_saida_cnj(cnj: str) -> str:
    return os.path.join(PASTA_SAIDA, f"{_nome_arquivo_seguro(cnj)}.json")


def _carregar_saida_cnj(cnj: str) -> dict:
    caminho = _caminho_saida_cnj(cnj)
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _salvar_saida_cnj(cnj: str, dados: dict) -> None:
    os.makedirs(PASTA_SAIDA, exist_ok=True)
    with open(_caminho_saida_cnj(cnj), "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# ============================================================
# MERGE — preenche "recuperanda" no database a partir da capa
# ============================================================

# Ordem de prioridade quando ha mais de um nome candidato.
_PADROES_PRIORIDADE_ATIVO = [
    re.compile(r"\bS[\s./]A\.?\b"),  # S A, S/A, S.A, S.A.
    re.compile(r"\bLTDA\.?\b"),
    re.compile(r"\bM[\s.]?E\.?\b"),  # ME, M E, M.E
]

# Nomes que sao claramente credores/interessados institucionais, nunca a
# recuperanda/falida (bancos, orgaos publicos, MP, fazenda...).
_PALAVRAS_INSTITUCIONAIS = [
    "MINISTERIO PUBLICO", "UNIAO FEDERAL", "ESTADO DE ", "ESTADO DO ",
    "FAZENDA NACIONAL", "FAZENDA PUBLICA", "PROCURADORIA", "PGFN", "PGE",
    "CAIXA ECONOMICA", "INSS", "RECEITA FEDERAL", "TRIBUNAL DE JUSTICA",
    "DEFENSORIA", "BANCO ", "ITAU", "BRADESCO", "SANTANDER", "UNIBANCO",
    "MUNICIPIO DE", "GOVERNO DO", "GOVERNO DE", "SECRETARIA DE ESTADO",
    "ADVOCACIA GERAL DA UNIAO", "JUSTICA FEDERAL",
]

RECUPERANDA_FONTE = "capa_pdpj"


def _e_institucional(nome: str) -> bool:
    norm = _normalizar(nome)
    return any(palavra in norm for palavra in _PALAVRAS_INSTITUCIONAIS)


def _melhor_nome(nomes: list[str]) -> str:
    """Descarta institucionais (se houver alternativa) e prioriza S/A > LTDA > ME."""
    candidatos = [n for n in nomes if not _e_institucional(n)] or nomes
    for padrao in _PADROES_PRIORIDADE_ATIVO:
        for nome in candidatos:
            if padrao.search(_normalizar(nome)):
                return nome
    return candidatos[0]


def escolher_recuperanda(polos: dict[str, list[str]], classe: str | None) -> str:
    """Escolhe qual parte da capa e a recuperanda/falida.
    Recuperacao Judicial so pode ser pedida pela propria empresa -> ela e ATIVO.
    Falencia requerida por credor -> quem pede e o credor (ATIVO); a empresa
    fica no PASSIVO. Nomes institucionais (bancos, MP, fazenda...) sao
    descartados quando houver alternativa, com sufixo S/A > LTDA > ME no
    desempate."""
    ativo = polos.get("ATIVO") or []
    passivo = polos.get("PASSIVO") or []
    classe_norm = _normalizar(classe or "")

    eh_falencia = "FALENCIA" in classe_norm
    eh_rj = "RECUPERACAO JUDICIAL" in classe_norm

    if eh_falencia and not eh_rj and passivo:
        return _melhor_nome(passivo)
    if ativo:
        return _melhor_nome(ativo)
    if passivo:
        return _melhor_nome(passivo)
    return ""


def mesclar_com_database(pasta_database: str = PASTA_DATABASE) -> None:
    """Preenche/corrige o campo 'recuperanda' em database/*.json a partir da
    capa salva em recuperandas_capa/{cnj}.json. Nao chama a PDPJ — usa somente
    o que ja foi salvo localmente. So mexe em classificacoes vazias ou que ja
    tinham sido preenchidas por este proprio script (recuperanda_fonte), nunca
    em valor extraido originalmente do texto da publicacao. Sobrescreve os
    arquivos alterados."""
    for caminho in sorted(glob.glob(os.path.join(pasta_database, "*.json"))):
        with open(caminho, encoding="utf-8") as f:
            itens = json.load(f)

        preenchidos = 0
        for item in itens:
            for classificacao in item.get("classificacoes") or []:
                if not classificacao.get("is_cessao_real"):
                    continue

                vazio = _campo_vazio(classificacao.get("recuperanda"))
                nosso = classificacao.get("recuperanda_fonte") == RECUPERANDA_FONTE
                if not (vazio or nosso):
                    continue

                cnj = (classificacao.get("cnj_rj") or item.get("cnj") or "").strip()
                if not cnj:
                    continue

                capa_dados = _carregar_saida_cnj(cnj)
                if capa_dados.get("status") != "ok":
                    continue

                escolha = escolher_recuperanda(capa_dados.get("polos") or {}, capa_dados.get("classe"))
                if not escolha:
                    continue

                classificacao["recuperanda"] = escolha
                classificacao["recuperanda_fonte"] = RECUPERANDA_FONTE
                preenchidos += 1

        if preenchidos:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(itens, f, ensure_ascii=False, indent=2)
        print(f"  [{os.path.basename(caminho)}] {preenchidos} recuperanda(s) preenchida(s)/corrigida(s).")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    global ACCESS_TOKEN, HEADERS

    arg = sys.argv[1].strip() if len(sys.argv) > 1 else None

    if arg == "--merge":
        # So mescla o que ja tem em recuperandas_capa/ no database — nao chama a PDPJ.
        print("=" * 60)
        print("PDPJ — Merge de recuperanda no database (sem consultar a PDPJ)")
        print("=" * 60)
        mesclar_com_database()
        return

    ACCESS_TOKEN = carregar_access_token()
    HEADERS = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if arg and _CNJ_REGEX.match(arg):
        # Modo teste: consulta um unico CNJ e apenas imprime (nao grava nada).
        print("=" * 60)
        print("PDPJ — Recuperanda via capa (modo teste, 1 CNJ)")
        print(f"CNJ: {arg}")
        print("=" * 60)
        resultado = processar_cnj_capa(arg)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    print("PDPJ — Recuperanda via capa dos autos")
    print("=" * 60)

    fontes_por_cnj = coletar_cnjs_sem_recuperanda()
    print(f"\n{len(fontes_por_cnj)} CNJs sem 'recuperanda' encontrados no database.")

    pendentes = [
        cnj for cnj in fontes_por_cnj
        if _carregar_saida_cnj(cnj).get("status") != "ok"
    ]
    print(f"{len(pendentes)} pendentes (ja resolvidos anteriormente sao pulados).")
    print(f"Pasta de saida: {PASTA_SAIDA}\n")

    processados = 0
    encontrados = 0
    try:
        for cnj in pendentes:
            processados += 1
            print(f"[{processados}/{len(pendentes)}] {cnj} ...")
            resultado = processar_cnj_capa(cnj, fontes_por_cnj[cnj])
            _salvar_saida_cnj(cnj, resultado)

            if resultado["status"] == "ok":
                encontrados += 1
                print(f"    OK — {resultado['recuperanda_capa']}")
            else:
                print(f"    {resultado['status'].upper()} — {resultado.get('erro', '')}")

            if processados < len(pendentes):
                time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n[INTERROMPIDO] Progresso ja salvo em {PASTA_SAIDA}/")

    print(f"\n{'=' * 60}")
    print(f"Concluido: {processados}/{len(pendentes)} CNJs processados nesta execucao")
    print(f"Recuperanda encontrada: {encontrados}/{processados}")
    print(f"Resultados em: {PASTA_SAIDA}")

    print(f"\n{'=' * 60}")
    print("Mesclando recuperanda no database...")
    print("=" * 60)
    mesclar_com_database()


if __name__ == "__main__":
    main()
