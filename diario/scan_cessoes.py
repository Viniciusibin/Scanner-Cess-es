"""
Scanner de cessões de crédito em contexto de RJ/Falência nos cadernos DJEN.
Filtro combinatório: keywords de cessão (fortes/fracas) + keywords de RJ.

Copia standalone para uso em monitoramento-diario/ — não depende de
nenhum outro arquivo do projeto.
"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── Keywords ─────────────────────────────────────────────────────────────────

# Fortes: bastam sozinhas para sinalizar cessão
KEYWORDS_CESSAO_FORTES = [
    "cessão de crédito",
    "cessionário",
    "cessionária",
    "substituição de credor",
    "cessão de direitos creditórios",
]

# Fracas: só contam como cessão se tiverem 2+ keywords de RJ
KEYWORDS_CESSAO_FRACAS = [
    "cedente",
    "sub-rogação",
    "transferência de crédito",
]

KEYWORDS_CESSAO_TODAS = KEYWORDS_CESSAO_FORTES + KEYWORDS_CESSAO_FRACAS

# Exclusão: pré-filtro que descarta antes de qualquer lógica, sem custo de LLM
KEYWORDS_EXCLUSAO = [
    "superendividamento",
    "lei nº 14.181",
    "repactuação de dívidas",
    "crédito consignado",
    "cartão de crédito ourocard",
    "cartão de benefício",
    "imposto sobre a propriedade",  # execução fiscal IPTU
    "cessão de quotas sociais",
    "cessão de cotas",
    "arbitramento de honorários",
    "duplicata mercantil",
    "endosso-mandato",
    "protesto indevido",
    "dano moral",
    "ação declaratória de inexistência",
    "arrolamento",
    "inventário judicial",
]

# Principal: sinal forte de RJ, basta 1 ocorrência (ou classe RJ)
KEYWORDS_RJ_PRINCIPAL = [
    "recuperação judicial",
    "quadro geral de credores",
    "administrador judicial",
    "plano de recuperação judicial",
    "assembleia geral de credores",
    "passivo concursal",
    "crédito concursal",
    "convolação em falência",
    "stay period",
    "recuperanda",
    "habilitação de crédito",
]

# Contexto: sinal fraco de RJ, exige 2+ para reforçar cessão forte
KEYWORDS_RJ_CONTEXTO = [
    "lei 11.101",
    "lei nº 11.101",
    "massa falida",
    "falência",
    "em recuperação judicial",
    "crédito extraconcursal",
    "recuperação extrajudicial",
]

# Mantém compatibilidade com código existente
KEYWORDS_RJ = KEYWORDS_RJ_PRINCIPAL + KEYWORDS_RJ_CONTEXTO

MIN_RJ_KEYWORDS_FOR_WEAK = 2  # mínimo de keywords RJ quando só tem cessão fraca

CLASSES_RJ = {"1112", "1141", "129"}


# ── Scanner ──────────────────────────────────────────────────────────────────

def match_keywords(texto: str, keywords: list[str]) -> list[str]:
    texto_lower = texto.lower()
    return [kw for kw in keywords if kw in texto_lower]


def classify(hits_fortes, hits_fracas, hits_rj_principal, hits_rj_contexto, is_classe_rj):
    """
    Retorna categoria ou None.
    - Cessão forte + RJ principal (1+ keyword ou classe) -> CESSAO_EM_RJ
    - Cessão forte + só RJ contexto (2+ keywords) -> CESSAO_EM_RJ
    - Cessão fraca + RJ robusto (classe ou 2+ keywords principais) -> CESSAO_EM_RJ
    - Demais combinações -> descarta (falso positivo)
    """
    has_forte = len(hits_fortes) > 0
    has_fraca = len(hits_fracas) > 0
    has_any_cessao = has_forte or has_fraca

    n_principal = len(hits_rj_principal)
    n_contexto = len(hits_rj_contexto)
    has_rj = n_principal > 0 or n_contexto > 0 or is_classe_rj

    if not has_any_cessao or not has_rj:
        return None

    if has_forte:
        # Cessão forte + RJ principal → passa sempre
        if n_principal > 0 or is_classe_rj:
            return "CESSAO_EM_RJ"
        # Cessão forte + só RJ contexto → exige 2+ keywords de contexto
        if n_contexto >= 2:
            return "CESSAO_EM_RJ"
        return None  # ex: "cessionário" + só "falência" → ruído

    # Só cessão fraca — exige RJ principal robusto ou classe
    if is_classe_rj or n_principal >= MIN_RJ_KEYWORDS_FOR_WEAK:
        return "CESSAO_EM_RJ"

    return None


def scan_arquivo(filepath: str) -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", data) if isinstance(data, dict) else data
    resultados = []

    for item in items:
        texto = item.get("texto", "")
        if not texto:
            continue

        # Pré-filtro de exclusão
        texto_lower = texto.lower()
        if any(kw in texto_lower for kw in KEYWORDS_EXCLUSAO):
            continue

        hits_fortes = match_keywords(texto, KEYWORDS_CESSAO_FORTES)
        hits_fracas = match_keywords(texto, KEYWORDS_CESSAO_FRACAS)
        hits_rj_principal = match_keywords(texto, KEYWORDS_RJ_PRINCIPAL)
        hits_rj_contexto = match_keywords(texto, KEYWORDS_RJ_CONTEXTO)
        hits_rj = hits_rj_principal + hits_rj_contexto  # para output
        classe = item.get("codigoClasse", "")
        is_classe_rj = classe in CLASSES_RJ

        categoria = classify(
            hits_fortes, hits_fracas,
            hits_rj_principal, hits_rj_contexto,
            is_classe_rj,
        )
        if categoria is None:
            continue

        resultados.append({
            "categoria": categoria,
            "id": item.get("id"),
            "cnj": item.get("numeroprocessocommascara", ""),
            "classe": item.get("nomeClasse", ""),
            "codigoClasse": classe,
            "orgao": item.get("nomeOrgao", ""),
            "tipo_doc": item.get("tipoDocumento", ""),
            "keywords_cessao_fortes": hits_fortes,
            "keywords_cessao_fracas": hits_fracas,
            "keywords_rj": hits_rj,
            "keywords_rj_principal": hits_rj_principal,
            "keywords_rj_contexto": hits_rj_contexto,
            "is_classe_rj": is_classe_rj,
            "destinatarios": [
                d.get("nome", "") for d in item.get("destinatarios", [])
            ],
            "texto_completo": texto,
            "link": item.get("link", ""),
        })

    return resultados


def scan_diretorio(diretorio: str) -> list[dict]:
    todos = []
    path = Path(diretorio)

    arquivos = sorted(path.glob("**/*.json"))
    if not arquivos:
        print(f"Nenhum JSON encontrado em {diretorio}")
        return todos

    for arq in arquivos:
        print(f"  Lendo {arq.name}...")
        resultados = scan_arquivo(str(arq))
        todos.extend(resultados)

    return todos


def imprimir_resultados(resultados: list[dict]):
    print(f"\n{'='*80}")
    print(f"CESSAO_EM_RJ: {len(resultados)} matches")
    print(f"{'='*80}")

    for r in resultados:
        print(f"\n  ID: {r['id']}")
        print(f"  CNJ: {r['cnj']}")
        print(f"  Classe: {r['classe']} ({r['codigoClasse']})")
        print(f"  Órgão: {r['orgao']}")
        print(f"  Tipo doc: {r['tipo_doc']}")
        print(f"  KW cessão fortes: {r['keywords_cessao_fortes']}")
        print(f"  KW cessão fracas: {r['keywords_cessao_fracas']}")
        print(f"  KW RJ ({len(r['keywords_rj'])}): {r['keywords_rj']}")
        print(f"  Classe RJ: {r['is_classe_rj']}")
        print(f"  Partes: {', '.join(r['destinatarios'][:3])}")
        print(f"  Link: {r['link']}")
        print(f"  Texto: {r['texto_completo'][:300]}...")
        print(f"  {'-'*78}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    diretorio = sys.argv[1] if len(sys.argv) > 1 else "."

    print(f"Scanning {diretorio}...")
    resultados = scan_diretorio(diretorio)
    imprimir_resultados(resultados)

    # Exporta só CESSAO_EM_RJ dentro do diretório de input
    output_file = Path(diretorio) / f"matches_{Path(diretorio).name}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)

    print(f"\nExportado: {output_file}")
