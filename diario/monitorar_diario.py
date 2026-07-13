"""
Monitoramento diário de cessões de crédito nos diários eletrônicos (DJEN).

Fluxo:
  1. Baixa e escaneia os cadernos mais recentes de cada tribunal (download_e_scan.py).
  2. Classifica os matches novos via IA (classificar_cessoes.py).
  3. Aplica filtro_cessoes.py + pdpj_capa.py + pdpj_valor_causa.py e faz o
     merge das cessões reais em database/TJ-<UF>-<ano>.json (merge_para_database.py).

Uso:
  python monitorar_diario.py                  # tribunais padrão, lookback automático
  python monitorar_diario.py TJMT TJSP        # só os tribunais informados
  python monitorar_diario.py --dias 3         # força um lookback fixo (ignora o automático)
  python monitorar_diario.py TJMT --dias 10

Lookback automático (padrão, quando --dias não é informado): para cada
tribunal, olha a data mais recente já presente em database/TJ-<UF>-*.json e
baixa/escaneia todos os dias entre ela e hoje — cobre qualquer intervalo sem
rodar (fins de semana, feriados, dias sem execução), sem precisar acertar um
número de dias manualmente. Se o tribunal ainda não tem nenhuma data
registrada, usa um teto de segurança (LOOKBACK_MAXIMO dias).

Também pode ser chamado programaticamente (usado pelo backend Flask):
  from monitorar_diario import executar_diario
  resumo = executar_diario(["TJSP"], on_progress=print)  # lookback automático
  resumo = executar_diario(["TJSP"], dias_lookback=10, on_progress=print)  # fixo
"""

import argparse
import glob
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATABASE_DIR = REPO_ROOT / "database"
sys.path.insert(0, str(SCRIPT_DIR))

from download_e_scan import baixar_e_escanear_recente, DADOS_DIR  # noqa: E402
from classificar_cessoes import classificar_lote  # noqa: E402
from merge_para_database import merge_cessoes_reais  # noqa: E402

TRIBUNAIS_PADRAO = [
    "TJBA", "TJGO", "TJMG", "TJMS", "TJMT",
    "TJPE", "TJPR", "TJRJ", "TJRS", "TJSP", "TJTO",
]

LOOKBACK_MINIMO = 1
LOOKBACK_MAXIMO = 60  # teto de segurança quando o tribunal não tem nenhuma data registrada ainda

OnProgress = Optional[Callable[[str, str], None]]


def _parse_data_flexivel(valor: str | None) -> "date | None":
    """'data' aparece em formatos diferentes conforme a origem do registro:
    ISO 'AAAA-MM-DD' (gravado pelo diario) ou 'DD/MM/AAAA' (import antigo)."""
    if not valor:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    return None


def _ultima_data_no_database(tribunal: str) -> "date | None":
    """Maior 'data' já presente em database/TJ-<UF>-*.json para este tribunal."""
    uf = tribunal[-2:].upper()
    maior: "date | None" = None
    for caminho in sorted(glob.glob(str(DATABASE_DIR / f"TJ-{uf}-*.json"))):
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                itens = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for item in itens:
            data = _parse_data_flexivel(item.get("data"))
            if data and (maior is None or data > maior):
                maior = data
    return maior


def _dias_lookback_automatico(tribunal: str) -> int:
    ultima = _ultima_data_no_database(tribunal)
    if ultima is None:
        return LOOKBACK_MAXIMO
    dias = (date.today() - ultima).days
    return max(LOOKBACK_MINIMO, min(dias, LOOKBACK_MAXIMO))


def coletar_matches_novos(resumos: list, tribunal: str) -> list:
    """Lê os matches_*.json apontados pelos resumos com status 'match' e
    marca cada item com o tribunal e a data (AAAA-MM-DD) do caderno de onde veio."""
    matches = []
    for r in resumos:
        if r.get("status") == "match":
            with open(r["matches_file"], "r", encoding="utf-8") as f:
                dados = json.load(f)
            for item in dados:
                item.setdefault("arquivo_origem", r["matches_file"])
                item.setdefault("tribunal", tribunal)
                item.setdefault("data", r["data"])
            matches.extend(dados)
    return matches


def executar_diario(tribunais: list, dias_lookback: "int | None" = None, on_progress: OnProgress = None) -> dict:
    """Executa a pipeline completa (baixar → escanear → classificar → mesclar)
    para cada tribunal. Devolve {tribunal: {matches, cessoes_reais,
    publicadas, novas_no_database, aviso}}.

    dias_lookback=None (padrão): calcula automaticamente por tribunal, a
    partir da última data já presente em database/TJ-<UF>-*.json até hoje.
    Passe um número para forçar um lookback fixo igual para todos."""
    hoje = date.today().isoformat()
    resumo_por_tribunal: dict[str, dict] = {}

    def progresso(tribunal: str, mensagem: str) -> None:
        if on_progress:
            on_progress(tribunal, mensagem)

    for tribunal in tribunais:
        lookback_tribunal = dias_lookback if dias_lookback is not None else _dias_lookback_automatico(tribunal)
        progresso(tribunal, f"baixando e escaneando cadernos (últimos {lookback_tribunal} dia(s))...")
        resumos = baixar_e_escanear_recente(tribunal, dias_lookback=lookback_tribunal)
        matches = coletar_matches_novos(resumos, tribunal)

        if not matches:
            progresso(tribunal, "nenhum match novo")
            resumo_por_tribunal[tribunal] = {
                "matches": 0,
                "cessoes_reais": 0,
                "publicadas": 0,
                "novas_no_database": 0,
                "aviso": None,
            }
            continue

        progresso(tribunal, f"classificando {len(matches)} match(es) via IA...")
        DADOS_DIR.mkdir(parents=True, exist_ok=True)
        output_file = DADOS_DIR / f"classificados_{tribunal}_{hoje}.json"
        cessoes_reais_file = DADOS_DIR / f"cessoes_reais_{tribunal}_{hoje}.json"
        classificar_lote(matches, output_file, cessoes_reais_file)

        with open(cessoes_reais_file, "r", encoding="utf-8") as f:
            cessoes_reais = json.load(f)

        progresso(tribunal, f"mesclando {len(cessoes_reais)} cessão(ões) real(is) no database...")
        resultado_merge = merge_cessoes_reais(tribunal, cessoes_reais)

        resumo_por_tribunal[tribunal] = {
            "matches": len(matches),
            "cessoes_reais": len(cessoes_reais),
            **resultado_merge,
        }
        progresso(tribunal, f"concluído — {resultado_merge['novas_no_database']} nova(s) no database")

    return resumo_por_tribunal


def monitorar(tribunais: list, dias_lookback: "int | None" = None) -> None:
    hoje = date.today().isoformat()
    print(f"{'='*70}\nMONITORAMENTO DIÁRIO DE CESSÕES - {hoje}\n{'='*70}")

    resumo = executar_diario(
        tribunais,
        dias_lookback=dias_lookback,
        on_progress=lambda tribunal, msg: print(f"[{tribunal}] {msg}"),
    )

    total_novas = sum(r["novas_no_database"] for r in resumo.values())
    avisos = [f"[{t}] {r['aviso']}" for t, r in resumo.items() if r.get("aviso")]

    print(f"\n{'='*70}\nRESUMO\n{'='*70}")
    for tribunal, r in resumo.items():
        print(f"  {tribunal}: {r['matches']} match(es), {r['cessoes_reais']} real(is), "
              f"{r['novas_no_database']} nova(s) no database")

    if total_novas:
        print(f"\n{'!'*70}")
        print(f"ALERTA: {total_novas} cessão(ões) nova(s) adicionada(s) ao database hoje!")
        print(f"{'!'*70}")
    else:
        print("\nNenhuma cessão nova adicionada ao database hoje.")

    for aviso in avisos:
        print(f"\nAVISO: {aviso}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitoramento diário de cessões de crédito nos DJEN.")
    parser.add_argument(
        "tribunais", nargs="*", default=TRIBUNAIS_PADRAO,
        help=f"Siglas dos tribunais a monitorar (padrão: {' '.join(TRIBUNAIS_PADRAO)})",
    )
    parser.add_argument(
        "--dias", type=int, default=None,
        help="Força um lookback fixo (em dias) para todos os tribunais. "
             "Se omitido (padrão), calcula automaticamente por tribunal a partir "
             "da última data já presente em database/TJ-<UF>-*.json.",
    )
    args = parser.parse_args()

    monitorar(args.tribunais, args.dias)


if __name__ == "__main__":
    main()
