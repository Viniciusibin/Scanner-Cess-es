import json
import html
import unicodedata
from collections import Counter, defaultdict

def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.upper().strip()

def esc(s):
    return html.escape(s or "")

def fmt_brl(v):
    if v is None:
        return "-"
    return "R$ " + f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")

def fmt_brl_compact(v):
    if v is None:
        return "-"
    if v >= 1_000_000_000:
        return f"R$ {v/1_000_000_000:.2f} bi".replace(".", ",")
    if v >= 1_000_000:
        return f"R$ {v/1_000_000:.1f} mi".replace(".", ",")
    return fmt_brl(v)

def fmt_recuperanda(s):
    if not s:
        return "-"
    return esc(s.split(",")[0].split("(")[0].strip())

with open("bb_cessoes_final.json", encoding="utf-8") as f:
    main = json.load(f)
with open("bb_travessia_final.json", encoding="utf-8") as f:
    trav = json.load(f)
with open("bb_ativos_final.json", encoding="utf-8") as f:
    ativ = json.load(f)

for grupo in (main, trav, ativ):
    grupo.sort(key=lambda r: (r["tribunal"], norm(r.get("recuperanda") or "")))

# ============================================================
# METRICAS GERAIS
# ============================================================
total_geral = len(main) + len(trav) + len(ativ)
todas_recuperandas = set()
for grupo in (main, trav, ativ):
    for d in grupo:
        if d.get("recuperanda"):
            todas_recuperandas.add(norm(d["recuperanda"]))
recuperandas_geral = len(todas_recuperandas)

soma_geral = sum(d["valor_causa"] for grupo in (main, trav, ativ) for d in grupo if d.get("valor_causa"))
soma_main = sum(d["valor_causa"] for d in main if d.get("valor_causa"))
soma_trav = sum(d["valor_causa"] for d in trav if d.get("valor_causa"))
soma_ativ = sum(d["valor_causa"] for d in ativ if d.get("valor_causa"))

tribunais_geral = Counter()
for grupo in (main, trav, ativ):
    for d in grupo:
        tribunais_geral[d["tribunal"]] += 1
tribunais_n = len(tribunais_geral)

# top 15 cessionarios combinados (por numero de cessoes), com soma de valor da causa
CAT_COR = {"inst": "var(--series-1)", "trav": "var(--series-2)", "ativ": "var(--series-3)"}
CAT_LABEL = {"inst": "Institucional diverso", "trav": "Travessia", "ativ": "Ativos S.A."}

agg = defaultdict(lambda: {"n": 0, "soma": 0.0, "cat": None})
for grupo, cat in [(main, "inst"), (trav, "trav"), (ativ, "ativ")]:
    for d in grupo:
        k = d.get("cessionario")
        if not k:
            continue
        agg[k]["n"] += 1
        agg[k]["soma"] += d.get("valor_causa") or 0
        agg[k]["cat"] = cat

top30 = sorted(agg.items(), key=lambda x: (-x[1]["n"], -x[1]["soma"]))[:30]

top_cess_rows = ""
for i, (nome, v) in enumerate(top30, start=1):
    cat = v["cat"]
    top_cess_rows += f"""
      <tr>
        <td class="rank">{i}</td>
        <td class="cessionario-nome">{esc(nome)}</td>
        <td class="num">{v['n']}</td>
        <td class="num">{fmt_brl(v['soma'])}</td>
      </tr>"""

trib_ordenado = tribunais_geral.most_common()
max_trib = trib_ordenado[0][1] if trib_ordenado else 1
trib_html = ""
for tj, n in trib_ordenado:
    largura = round(100 * n / max_trib)
    trib_html += f"""
      <div class="bar-row">
        <div class="bar-label">{tj}</div>
        <div class="bar-track"><div class="bar-fill" style="width:{largura}%; background:var(--series-1)"></div></div>
        <div class="bar-value">{n}</div>
      </div>"""

# ============================================================
# TABELAS POR ABA
# ============================================================

def linhas_tabela(dados):
    linhas = []
    for d in dados:
        valor = d.get("valor") or ""
        valor_curto = valor.split("(")[0].strip() if valor else "-"
        vc = d.get("valor_causa")
        valor_causa_fmt = fmt_brl(vc)
        vc_sort = vc if vc is not None else -1
        tribunal = d["tribunal"]
        cessionario = d.get("cessionario") or ""
        original = d.get("cessionario_original") or ""
        title_attr = f' title="{esc(original)}"' if original and original != cessionario else ""
        cess_html = esc(cessionario) if cessionario else '<span class="muted">não identificado</span>'
        recuperanda_txt = fmt_recuperanda(d.get("recuperanda"))
        cnj = d["cnj"]
        linhas.append(f"""
      <tr data-tribunal="{esc(tribunal)}" data-cessionario="{esc(cessionario)}"
          data-s-tribunal="{esc(tribunal)}" data-s-recuperanda="{esc(norm(d.get('recuperanda')))}"
          data-s-cnj="{esc(cnj)}" data-s-cessionario="{esc(norm(cessionario))}" data-s-valorcausa="{vc_sort}">
        <td class="tj"><span class="chip">{esc(tribunal)}</span></td>
        <td class="recuperanda">{recuperanda_txt}</td>
        <td class="cnj">{esc(cnj)}</td>
        <td class="cessionario"{title_attr}>{cess_html}</td>
        <td class="valor num">{esc(valor_curto)}</td>
        <td class="valor num">{valor_causa_fmt}</td>
      </tr>""")
    return "".join(linhas)

def opcoes_tribunal(dados):
    cont = Counter(d["tribunal"] for d in dados)
    opts = [f'<option value="all">Todos os tribunais · {len(dados)}</option>']
    for tj, n in sorted(cont.items(), key=lambda x: -x[1]):
        opts.append(f'<option value="{esc(tj)}">{esc(tj)} · {n}</option>')
    return "\n".join(opts)

def opcoes_cessionario(dados):
    cont = Counter(d["cessionario"] for d in dados if d.get("cessionario"))
    opts = ['<option value="all">Todas as séries</option>']
    for c, n in sorted(cont.items(), key=lambda x: -x[1]):
        opts.append(f'<option value="{esc(c)}">{esc(c)} · {n}</option>')
    return "\n".join(opts)

def stat_tile(n, label):
    return f'<div class="stat"><div class="n">{n}</div><div class="l">{label}</div></div>'

def bloco_stats(dados):
    total = len(dados)
    recuperandas = len(set(norm(d.get("recuperanda")) for d in dados if d.get("recuperanda")))
    cessionarios = len(set(d.get("cessionario") for d in dados if d.get("cessionario")))
    soma = sum(d["valor_causa"] for d in dados if d.get("valor_causa"))
    com_valor = sum(1 for d in dados if d.get("valor_causa"))
    out = '<div class="stats">'
    out += stat_tile(total, "cessões identificadas")
    out += stat_tile(recuperandas, "recuperandas / massas falidas distintas")
    out += stat_tile(cessionarios, "cessionários distintos (padronizados)")
    out += stat_tile(fmt_brl(soma), f"soma do valor da causa ({com_valor} de {total} casos)")
    out += "</div>"
    return out


main_stats = bloco_stats(main)
trav_stats = bloco_stats(trav)
ativ_stats = bloco_stats(ativ)

main_opts_tj = opcoes_tribunal(main)
trav_opts_tj = opcoes_tribunal(trav)
ativ_opts_tj = opcoes_tribunal(ativ)
trav_opts_cs = opcoes_cessionario(trav)

main_rows = linhas_tabela(main)
trav_rows = linhas_tabela(trav)
ativ_rows = linhas_tabela(ativ)

TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Cessões Banco do Brasil</title>
<style>
:root {{
  color-scheme: light;
  --bg: #FFFFFF;
  --surface: #FFFFFF;
  --surface-2: #F4F6F9;
  --surface-3: #E9EDF3;
  --ink: #121A2B;
  --muted: #5B6472;
  --muted-2: #7C8698;
  --border: #E1E6ED;
  --border-strong: #C9D1DD;
  --accent: #0B3D91;
  --accent-ink: #082B69;
  --accent-soft: #E7EEF9;
  --row-hover: #F7F9FC;
  --shadow: 0 1px 2px rgba(18,26,43,0.04), 0 6px 20px rgba(18,26,43,0.06);
  --shadow-lg: 0 2px 4px rgba(18,26,43,0.05), 0 16px 40px rgba(18,26,43,0.09);
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --series-3: #1baf7a;
  --radius: 12px;
  --radius-sm: 8px;
}}

* {{ box-sizing: border-box; }}

body {{
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}

.wrap {{ max-width: 1320px; margin: 0 auto; padding: 44px 36px 90px; }}

header.page {{ margin-bottom: 8px; }}

.eyebrow {{
  font-size: 12px;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--accent-ink);
  font-weight: 600;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}}

.eyebrow::before {{
  content: "";
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--accent);
  display: inline-block;
}}

h1 {{
  font-family: Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif;
  font-size: clamp(28px, 3.4vw, 40px);
  font-weight: 600;
  margin: 0 0 14px;
  text-wrap: balance;
  letter-spacing: -0.015em;
  color: var(--ink);
}}

h2 {{
  font-family: Georgia, "Iowan Old Style", serif;
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 3px;
  color: var(--ink);
}}

.subtitle {{ max-width: 74ch; color: var(--muted); font-size: 15px; margin: 0 0 28px; }}

/* ---------- Tabs ---------- */
.tabs {{
  display: flex;
  gap: 2px;
  background: var(--surface-2);
  padding: 4px;
  border-radius: 10px;
  margin: 0 0 28px;
  width: fit-content;
  max-width: 100%;
  overflow-x: auto;
}}

.tab-btn {{
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  padding: 9px 16px;
  background: none;
  border: none;
  border-radius: 7px;
  color: var(--muted);
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.12s ease, color 0.12s ease;
}}

.tab-btn:hover {{ color: var(--ink); }}

.tab-btn.active {{
  color: var(--ink);
  background: var(--surface);
  box-shadow: var(--shadow);
}}

.tab-btn:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

.tab-panel {{ display: none; }}
.tab-panel.active {{ display: block; animation: fadein 0.15s ease; }}

@keyframes fadein {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}

/* ---------- Stats ---------- */
.stats {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  margin: 0 0 26px;
}}

.stat {{ background: var(--surface); padding: 18px 20px; }}

.stat .n {{
  font-family: Georgia, serif;
  font-size: clamp(19px, 2vw, 26px);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
  white-space: nowrap;
}}

.stat .l {{ font-size: 12px; color: var(--muted); margin-top: 5px; line-height: 1.4; }}

.section {{ margin-bottom: 36px; }}
.section-head {{ margin-bottom: 14px; }}
.section-head p {{ margin: 3px 0 0; color: var(--muted); font-size: 13px; }}

.legend {{ display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 14px; font-size: 12.5px; color: var(--muted); }}
.legend-item {{ display: flex; align-items: center; gap: 6px; }}
.legend-dot {{ width: 8px; height: 8px; border-radius: 2px; display: inline-block; }}

.chart-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 22px 24px;
  box-shadow: var(--shadow);
}}

.bar-row {{
  display: grid;
  grid-template-columns: minmax(140px, 260px) 1fr 60px;
  align-items: center;
  gap: 14px;
  padding: 8px 0;
  font-size: 13px;
}}

.bar-label {{ color: var(--ink); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.bar-tag {{ font-size: 10.5px; font-weight: 600; padding: 2px 7px; border-radius: 5px; margin-left: 7px; white-space: nowrap; }}
.bar-track {{ height: 7px; background: var(--surface-3); border-radius: 4px; overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 4px; }}
.bar-value {{ text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); font-weight: 600; font-size: 12.5px; }}

.grid-2 {{ display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 22px; }}
@media (max-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}

.note {{
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-sm);
  padding: 13px 18px;
  font-size: 13px;
  color: var(--muted);
  margin: 0 0 22px;
}}

.note strong {{ color: var(--ink); }}

/* ---------- Toolbar (busca + filtros em dropdown) ---------- */
.toolbar {{
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding: 12px 14px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}}

.search-field {{
  position: relative;
  flex: 1 1 260px;
  min-width: 200px;
}}

.search-field svg {{
  position: absolute;
  left: 11px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--muted-2);
  pointer-events: none;
}}

.search {{
  width: 100%;
  padding: 9px 12px 9px 34px;
  border-radius: 7px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--ink);
  font: inherit;
  font-size: 13.5px;
}}

.search:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 1px; }}

.select-field select {{
  font: inherit;
  font-size: 13.5px;
  padding: 9px 30px 9px 12px;
  border-radius: 7px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--ink);
  cursor: pointer;
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%236B6862' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  max-width: 240px;
}}

.select-field select:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 1px; }}

.result-count {{
  font-size: 12.5px;
  color: var(--muted);
  white-space: nowrap;
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}}

.clear-btn {{
  font: inherit;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 12px;
  border-radius: 7px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
}}
.clear-btn:hover {{ color: var(--accent-ink); border-color: var(--accent); }}

/* ---------- Table ---------- */
.table-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow-x: auto;
  box-shadow: var(--shadow);
}}

table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; min-width: 980px; }}

thead th {{
  background: var(--surface-2);
  text-align: left;
  padding: 12px 14px;
  font-size: 11px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
  border-bottom: 1px solid var(--border-strong);
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
}}

thead th:hover {{ color: var(--ink); background: var(--surface-3); }}

thead th .sort-arrow {{
  display: inline-block;
  margin-left: 4px;
  opacity: 0.35;
  font-size: 10px;
}}

thead th.sorted .sort-arrow {{ opacity: 1; color: var(--accent-ink); }}
thead th.sorted {{ color: var(--ink); }}

tbody td {{ padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }}
tbody tr:nth-child(even) {{ background: rgba(0,0,0,0.011); }}
tbody tr:hover {{ background: var(--row-hover); }}
tbody tr:last-child td {{ border-bottom: none; }}

.chip {{
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 5px;
  background: var(--accent-soft);
  color: var(--accent-ink);
  font-variant-numeric: tabular-nums;
}}

.recuperanda {{ font-weight: 500; max-width: 210px; }}
.cnj {{ font-variant-numeric: tabular-nums; color: var(--muted); white-space: nowrap; font-size: 12.5px; }}
.cessionario {{ max-width: 260px; cursor: help; }}
.valor.num, td.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; text-align: right; }}
.muted {{ color: var(--muted); font-style: italic; }}

footer {{ margin-top: 22px; font-size: 11.5px; color: var(--muted-2); }}

/* ---------- Top cessionarios (tabela) ---------- */
.rank-table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
.rank-table thead th {{ position: static; cursor: default; }}
.rank-table thead th:hover {{ background: none; color: var(--muted); }}
.rank-table td {{ padding: 10px 6px; border-bottom: 1px solid var(--border); }}
.rank-table tr:last-child td {{ border-bottom: none; }}
.rank-table .rank {{
  font-family: Georgia, serif;
  color: var(--muted-2);
  font-size: 13px;
  width: 28px;
}}
.cessionario-nome {{ font-weight: 500; }}

@media (max-width: 640px) {{
  .wrap {{ padding: 26px 16px 60px; }}
  .bar-row {{ grid-template-columns: 100px 1fr 46px; }}
  .grid-2 {{ gap: 16px; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header class="page">
    <div class="eyebrow">Monitoramento de cessões · Diário de Justiça Eletrônico</div>
    <h1>Créditos cedidos pelo Banco do Brasil</h1>
    <p class="subtitle">Todos os casos de recuperação judicial e falência em que o Banco do Brasil figura como cedente, identificados a partir de publicações do DJe e confirmados por classificação assistida. Organizado por tipo de cessionário.</p>
  </header>

  <nav class="tabs" role="tablist">
    <button class="tab-btn active" data-tab="geral" role="tab">Visão geral</button>
    <button class="tab-btn" data-tab="main" role="tab">Institucionais · {n_main}</button>
    <button class="tab-btn" data-tab="trav" role="tab">BB × Travessia · {n_trav}</button>
    <button class="tab-btn" data-tab="ativ" role="tab">BB × Ativos · {n_ativ}</button>
  </nav>

  <!-- ===================== GERAL ===================== -->
  <section class="tab-panel active" id="tab-geral">
    <div class="stats">
      {stat_total}
      {stat_recuperandas}
      {stat_tribunais}
      {stat_soma}
    </div>

    <div class="note">
      <strong>Por que separar em abas:</strong> Travessia Securitizadora e Ativos S.A. Securitizadora são plataformas de compra de crédito em varejo/massa — cada uma sozinha responde por mais casos que qualquer FIDC institucional individual. Isolá-las evita que dominem a leitura de "quem mais compra crédito distressed do BB" na aba de cessionários institucionais.
    </div>

    <div class="grid-2">
      <div class="section">
        <div class="section-head">
          <h2>Cessões por categoria de cessionário</h2>
          <p>{n_geral} cessões no total — {n_main} institucionais diversas + {n_trav} Travessia + {n_ativ} Ativos</p>
        </div>
        <div class="chart-card">
          <div class="bar-row">
            <div class="bar-label">Institucionais diversos <span class="bar-tag" style="background:var(--series-1)1a;color:var(--series-1)">{n_main}</span></div>
            <div class="bar-track"><div class="bar-fill" style="width:{pct_main}%; background:var(--series-1)"></div></div>
            <div class="bar-value">{soma_main_compact}</div>
          </div>
          <div class="bar-row">
            <div class="bar-label">Travessia <span class="bar-tag" style="background:var(--series-2)1a;color:var(--series-2)">{n_trav}</span></div>
            <div class="bar-track"><div class="bar-fill" style="width:{pct_trav}%; background:var(--series-2)"></div></div>
            <div class="bar-value">{soma_trav_compact}</div>
          </div>
          <div class="bar-row">
            <div class="bar-label">Ativos S.A. <span class="bar-tag" style="background:var(--series-3)1a;color:var(--series-3)">{n_ativ}</span></div>
            <div class="bar-track"><div class="bar-fill" style="width:{pct_ativ}%; background:var(--series-3)"></div></div>
            <div class="bar-value">{soma_ativ_compact}</div>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>Cessões por tribunal</h2>
          <p>{tribunais_n} TJs monitorados, tribunal corrigido a partir do CNJ</p>
        </div>
        <div class="chart-card">
          {trib_chart}
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-head">
        <h2>Maiores cessionários — todas as categorias</h2>
        <p>Top 30 por número de cessões, com o valor da causa somado ao lado</p>
      </div>
      <div class="chart-card">
        <table class="rank-table">
          <thead>
            <tr>
              <th></th>
              <th>Cessionário</th>
              <th style="text-align:right">Nº de cessões</th>
              <th style="text-align:right">Valor da causa (soma)</th>
            </tr>
          </thead>
          <tbody>{top_cess_rows}</tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- ===================== MAIN (institucionais) ===================== -->
  <section class="tab-panel" id="tab-main">
    {main_stats}
    <div class="note">
      <strong>Escopo:</strong> exclui Travessia Securitizadora e Ativos S.A. Securitizadora (veja abas dedicadas). Nomes de cessionários padronizados — passe o mouse para ver a grafia original da publicação.
    </div>
    <div class="toolbar">
      <div class="search-field">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="6" cy="6" r="5" stroke="currentColor" stroke-width="1.4"/><path d="M9.6 9.6L13 13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        <input class="search" data-search-scope="main" type="text" placeholder="Buscar por recuperanda, CNJ ou cessionário…">
      </div>
      <div class="select-field">
        <select data-select-scope="main" data-attr="tribunal">{main_opts_tj}</select>
      </div>
      <button class="clear-btn" data-clear-scope="main">Limpar filtros</button>
      <span class="result-count" data-count-scope="main"></span>
    </div>
    <div class="table-container">
      <table data-table="main">
        <thead>
          <tr>
            <th data-key="s-tribunal">TJ<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-recuperanda">Recuperanda / massa falida<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-cnj">CNJ<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-cessionario">Cessionário (padronizado)<span class="sort-arrow">▲▼</span></th>
            <th style="text-align:right">Valor mencionado</th>
            <th style="text-align:right" data-key="s-valorcausa">Valor da causa<span class="sort-arrow">▲▼</span></th>
          </tr>
        </thead>
        <tbody>{main_rows}</tbody>
      </table>
    </div>
  </section>

  <!-- ===================== TRAVESSIA ===================== -->
  <section class="tab-panel" id="tab-trav">
    {trav_stats}
    <div class="note">
      <strong>Séries identificadas:</strong> Travessia opera por veículos de securitização distintos (VIII, X, Mercantis XXV, Mercantis SHP) — mantidos separados por serem entidades legais diferentes, não uma variação de grafia.
    </div>
    <div class="toolbar">
      <div class="search-field">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="6" cy="6" r="5" stroke="currentColor" stroke-width="1.4"/><path d="M9.6 9.6L13 13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        <input class="search" data-search-scope="trav" type="text" placeholder="Buscar por recuperanda, CNJ ou série…">
      </div>
      <div class="select-field">
        <select data-select-scope="trav" data-attr="tribunal">{trav_opts_tj}</select>
      </div>
      <div class="select-field">
        <select data-select-scope="trav" data-attr="cessionario">{trav_opts_cs}</select>
      </div>
      <button class="clear-btn" data-clear-scope="trav">Limpar filtros</button>
      <span class="result-count" data-count-scope="trav"></span>
    </div>
    <div class="table-container">
      <table data-table="trav">
        <thead>
          <tr>
            <th data-key="s-tribunal">TJ<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-recuperanda">Recuperanda / massa falida<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-cnj">CNJ<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-cessionario">Cessionário (padronizado)<span class="sort-arrow">▲▼</span></th>
            <th style="text-align:right">Valor mencionado</th>
            <th style="text-align:right" data-key="s-valorcausa">Valor da causa<span class="sort-arrow">▲▼</span></th>
          </tr>
        </thead>
        <tbody>{trav_rows}</tbody>
      </table>
    </div>
  </section>

  <!-- ===================== ATIVOS ===================== -->
  <section class="tab-panel" id="tab-ativ">
    {ativ_stats}
    <div class="note">
      <strong>Cessionário único:</strong> todos os 21 registros de grafia distinta encontrados nas publicações correspondem à mesma empresa, Ativos S.A. Securitizadora de Créditos Financeiros.
    </div>
    <div class="toolbar">
      <div class="search-field">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="6" cy="6" r="5" stroke="currentColor" stroke-width="1.4"/><path d="M9.6 9.6L13 13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        <input class="search" data-search-scope="ativ" type="text" placeholder="Buscar por recuperanda ou CNJ…">
      </div>
      <div class="select-field">
        <select data-select-scope="ativ" data-attr="tribunal">{ativ_opts_tj}</select>
      </div>
      <button class="clear-btn" data-clear-scope="ativ">Limpar filtros</button>
      <span class="result-count" data-count-scope="ativ"></span>
    </div>
    <div class="table-container">
      <table data-table="ativ">
        <thead>
          <tr>
            <th data-key="s-tribunal">TJ<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-recuperanda">Recuperanda / massa falida<span class="sort-arrow">▲▼</span></th>
            <th data-key="s-cnj">CNJ<span class="sort-arrow">▲▼</span></th>
            <th>Cessionário</th>
            <th style="text-align:right">Valor mencionado</th>
            <th style="text-align:right" data-key="s-valorcausa">Valor da causa<span class="sort-arrow">▲▼</span></th>
          </tr>
        </thead>
        <tbody>{ativ_rows}</tbody>
      </table>
    </div>
  </section>

  <footer>Base: database/*.json (pipeline diario/classificar_cessoes.py). Deduplicado por CNJ + cessionário. "Valor da causa" via campo valor_causa do processo (PDPJ). Tribunal derivado do número CNJ. Arquivo local — gerado por build_dashboard.py.</footer>
</div>

<script>
// --- Tabs ---
const tabBtns = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');
tabBtns.forEach(btn => {{
  btn.addEventListener('click', () => {{
    tabBtns.forEach(b => b.classList.remove('active'));
    tabPanels.forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  }});
}});

// --- Filtros (dropdown) + busca + contador, por tabela ---
function setupTable(scope) {{
  const table = document.querySelector(`table[data-table="${{scope}}"]`);
  if (!table) return;
  const rows = Array.from(table.querySelectorAll('tbody tr'));
  const searchInput = document.querySelector(`input[data-search-scope="${{scope}}"]`);
  const selects = document.querySelectorAll(`select[data-select-scope="${{scope}}"]`);
  const clearBtn = document.querySelector(`button[data-clear-scope="${{scope}}"]`);
  const countEl = document.querySelector(`span[data-count-scope="${{scope}}"]`);

  function applyFilters() {{
    const q = (searchInput ? searchInput.value.trim().toLowerCase() : '');
    let visivel = 0;
    rows.forEach(row => {{
      let ok = true;
      selects.forEach(sel => {{
        if (sel.value === 'all') return;
        const attr = sel.dataset.attr;
        if (row.dataset[attr] !== sel.value) ok = false;
      }});
      if (ok && q) ok = row.textContent.toLowerCase().includes(q);
      row.style.display = ok ? '' : 'none';
      if (ok) visivel++;
    }});
    if (countEl) countEl.textContent = `${{visivel}} de ${{rows.length}} registros`;
  }}

  if (searchInput) searchInput.addEventListener('input', applyFilters);
  selects.forEach(sel => sel.addEventListener('change', applyFilters));
  if (clearBtn) clearBtn.addEventListener('click', () => {{
    if (searchInput) searchInput.value = '';
    selects.forEach(sel => sel.value = 'all');
    applyFilters();
  }});

  applyFilters();

  // --- Ordenacao por coluna ---
  const headers = table.querySelectorAll('thead th[data-key]');
  let sortState = {{ key: null, dir: 1 }};
  headers.forEach(th => {{
    th.addEventListener('click', () => {{
      const key = th.dataset.key;
      const dir = (sortState.key === key) ? -sortState.dir : 1;
      sortState = {{ key, dir }};
      headers.forEach(h => h.classList.remove('sorted'));
      th.classList.add('sorted');

      const tbody = table.querySelector('tbody');
      const numeric = key === 's-valorcausa';
      rows.sort((a, b) => {{
        let va = a.dataset[toCamel(key)] || '';
        let vb = b.dataset[toCamel(key)] || '';
        if (numeric) {{
          va = parseFloat(va) || -1;
          vb = parseFloat(vb) || -1;
          return (va - vb) * dir;
        }}
        return va.localeCompare(vb, 'pt-BR') * dir;
      }});
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});

  function toCamel(k) {{
    return k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  }}
}}

setupTable('main');
setupTable('trav');
setupTable('ativ');
</script>
</body>
</html>
"""

pct_main = round(100 * len(main) / total_geral)
pct_trav = round(100 * len(trav) / total_geral)
pct_ativ = round(100 * len(ativ) / total_geral)

output = TEMPLATE.format(
    n_main=len(main), n_trav=len(trav), n_ativ=len(ativ), n_geral=total_geral,
    stat_total=stat_tile(total_geral, "cessões identificadas (geral)"),
    stat_recuperandas=stat_tile(recuperandas_geral, "recuperandas / massas falidas distintas"),
    stat_tribunais=stat_tile(tribunais_n, "tribunais (TJs)"),
    stat_soma=stat_tile(fmt_brl(soma_geral), "soma do valor da causa (geral)"),
    pct_main=pct_main, pct_trav=pct_trav, pct_ativ=pct_ativ,
    soma_main_compact=fmt_brl_compact(soma_main),
    soma_trav_compact=fmt_brl_compact(soma_trav),
    soma_ativ_compact=fmt_brl_compact(soma_ativ),
    tribunais_n=tribunais_n,
    trib_chart=trib_html,
    top_cess_rows=top_cess_rows,
    main_stats=main_stats, trav_stats=trav_stats, ativ_stats=ativ_stats,
    main_opts_tj=main_opts_tj, trav_opts_tj=trav_opts_tj, ativ_opts_tj=ativ_opts_tj,
    trav_opts_cs=trav_opts_cs,
    main_rows=main_rows, trav_rows=trav_rows, ativ_rows=ativ_rows,
)

with open("bb_cessoes_dashboard.html", "w", encoding="utf-8") as f:
    f.write(output)

print("gerado bb_cessoes_dashboard.html,", len(output), "bytes")
