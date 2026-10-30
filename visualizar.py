#[imports]
import argparse
import glob
import json
import math
import os
import sys

from ag import LIMITE_CIDADES, custoRota, matrizDistancias, rotaValida


#limites e cores
TAMANHO_MAXIMO_ARQUIVO = 20 * 1024 * 1024
JANELA_EVENTOS = 5.0
LIDERANCA_ESTAVEL = 2.0
AQUECIMENTO_LIDER = 1.0
FOLGA_ROTULOS = 2.2
CORES_SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SUPERFICIE = "#fcfcfb"
TINTA = "#0b0b0b"
TINTA_SECUNDARIA = "#52514e"
TINTA_SUAVE = "#898781"
GRADE = "#e1e0d9"
EIXO = "#c3c2b7"
CRITICO = "#d03b3b"


#validacao
def ehNumero(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def exigir(condicao, mensagem):
    if not condicao:
        raise ValueError(mensagem)


def validarResultado(dados):
    exigir(isinstance(dados, dict), "conteudo nao e um objeto JSON")
    exigir(isinstance(dados.get("id"), int) and not isinstance(dados.get("id"), bool), "campo 'id' invalido")
    exigir(isinstance(dados.get("problema"), str), "campo 'problema' invalido")
    exigir(ehNumero(dados.get("inicio")) and ehNumero(dados.get("fim")), "campos 'inicio'/'fim' invalidos")
    exigir(isinstance(dados.get("lider"), bool), "campo 'lider' invalido")

    cidades = dados.get("cidades")
    exigir(isinstance(cidades, list) and LIMITE_CIDADES[0] <= len(cidades) <= LIMITE_CIDADES[1], "campo 'cidades' invalido")
    exigir(all(isinstance(c, list) and len(c) == 2 and all(ehNumero(v) for v in c) for c in cidades), "coordenada de cidade invalida")
    matriz = matrizDistancias(cidades)

    def validarRota(bloco, nome):
        exigir(isinstance(bloco, dict) and ehNumero(bloco.get("custo")), f"campo '{nome}' invalido")
        exigir(rotaValida(bloco.get("rota"), len(cidades)), f"rota de '{nome}' invalida")
        custoReal = custoRota(bloco["rota"], matriz)
        exigir(abs(custoReal - bloco["custo"]) <= 1e-6 * max(1.0, custoReal), f"custo de '{nome}' nao confere com a rota")

    validarRota(dados.get("melhorLocal"), "melhorLocal")
    if dados.get("melhorGlobal") is not None:
        validarRota(dados["melhorGlobal"], "melhorGlobal")
        exigir(isinstance(dados["melhorGlobal"].get("origem"), int), "origem do melhorGlobal invalida")

    for nome, tamanho in (("historico", 3), ("historicoGlobal", 3)):
        lista = dados.get(nome)
        exigir(isinstance(lista, list), f"campo '{nome}' invalido")
        exigir(all(isinstance(p, list) and len(p) == tamanho and all(ehNumero(v) for v in p) for p in lista), f"ponto invalido em '{nome}'")

    eventos = dados.get("eventos")
    exigir(isinstance(eventos, list), "campo 'eventos' invalido")
    exigir(all(isinstance(e, list) and len(e) == 3 and ehNumero(e[0]) and e[1] in ("queda", "lideranca") and isinstance(e[2], int)
               for e in eventos), "evento invalido")

    return dados


def carregarResultados(pasta):
    resultados = []
    for caminho in sorted(glob.glob(os.path.join(pasta, "no*.json"))):
        try:
            if os.path.getsize(caminho) > TAMANHO_MAXIMO_ARQUIVO:
                raise ValueError(f"arquivo maior que {TAMANHO_MAXIMO_ARQUIVO // (1024 * 1024)} MB")
            with open(caminho, encoding="utf-8") as arquivo:
                resultados.append(validarResultado(json.load(arquivo)))
        except (OSError, json.JSONDecodeError, ValueError) as erro:
            print(f"aviso: ignorando {caminho}: {erro}")
    return resultados


def selecionarExecucao(resultados):
    lideres = [r for r in resultados if r["lider"] and r["melhorGlobal"]]
    referencia = max(lideres or resultados, key=lambda r: r["fim"])

    execucao = []
    for r in resultados:
        mesmaExecucao = (r["problema"] == referencia["problema"] and r["cidades"] == referencia["cidades"]
                         and r["inicio"] <= referencia["fim"] and r["fim"] >= referencia["inicio"])
        if mesmaExecucao:
            execucao.append(r)
        else:
            print(f"aviso: no{r['id']}.json e de outra execucao, ignorado")

    return referencia, sorted(execucao, key=lambda r: r["id"])


#eventos
def primeiraOcorrencia(eventos):
    resultado = []
    ultimo = {}
    for t, lider in eventos:
        if t - ultimo.get(lider, -math.inf) > JANELA_EVENTOS:
            resultado.append((t, lider))
        ultimo[lider] = t
    return resultado


def anotacoesDeEventos(execucao, t0):
    quedas = sorted((e[0], e[2]) for r in execucao for e in r["eventos"] if e[1] == "queda")
    liderancas = sorted((e[0], e[2]) for r in execucao for e in r["eventos"] if e[1] == "lideranca")

    eventos = []
    for t, lider in primeiraOcorrencia(quedas):
        eventos.append((t - t0, "queda", f"lider {lider} caiu"))

    liderancas = primeiraOcorrencia(liderancas)
    for i, (t, lider) in enumerate(liderancas):
        proxima = liderancas[i + 1][0] if i + 1 < len(liderancas) else math.inf
        if proxima - t >= LIDERANCA_ESTAVEL:
            eventos.append((t - t0, "lideranca", f"no {lider} assume"))

    eventos.sort()
    agrupados = []
    for t, tipo, texto in eventos:
        if agrupados and t - agrupados[-1][0] <= JANELA_EVENTOS:
            agrupados[-1][1].append((t, tipo))
            agrupados[-1][2].append(texto)
        else:
            agrupados.append([t, [(t, tipo)], [texto]])

    return agrupados


#desenho
def descartarAquecimento(historicoGlobal):
    if not historicoGlobal:
        return []
    corte = historicoGlobal[0][0] + AQUECIMENTO_LIDER
    antes = [p for p in historicoGlobal if p[0] <= corte]
    depois = [p for p in historicoGlobal if p[0] > corte]
    return [[corte, antes[-1][1], antes[-1][2]]] + depois


def corDoNo(idNo):
    return CORES_SERIES[(idNo - 1) % len(CORES_SERIES)]


def estiloDoNo(idNo):
    return "-" if (idNo - 1) < len(CORES_SERIES) else "--"


def estilizarEixo(eixo):
    eixo.set_facecolor(SUPERFICIE)
    for lado in ("top", "right"):
        eixo.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        eixo.spines[lado].set_color(EIXO)
    eixo.tick_params(colors=TINTA_SUAVE, labelsize=9)


def desenharMapa(eixo, cidades, rota, custo, origem):
    estilizarEixo(eixo)
    fechada = rota + [rota[0]]
    eixo.plot([cidades[i][0] for i in fechada], [cidades[i][1] for i in fechada], color=corDoNo(origem), linewidth=1.6, zorder=2)
    eixo.scatter([c[0] for c in cidades], [c[1] for c in cidades], s=16, color=TINTA, edgecolors=SUPERFICIE, linewidths=0.8, zorder=3)

    eixo.set_aspect("equal")
    eixo.set_xticks([])
    eixo.set_yticks([])
    for lado in ("left", "bottom"):
        eixo.spines[lado].set_visible(False)
    eixo.set_title(f"Melhor rota: custo {custo:.2f}\n{len(cidades)} cidades · encontrada pelo no {origem}",
                   loc="left", fontsize=11, color=TINTA)


def desenharEvolucao(eixo, referencia, execucao, t0):
    from matplotlib.ticker import FuncFormatter, LogLocator

    estilizarEixo(eixo)
    for r in execucao:
        if not r["historico"]:
            continue
        tempos = [p[0] - t0 for p in r["historico"]]
        custos = [p[2] for p in r["historico"]]
        eixo.step(tempos, custos, where="post", color=corDoNo(r["id"]), linestyle=estiloDoNo(r["id"]),
                  linewidth=1.6, label=f"ilha do no {r['id']}", zorder=2)

    historicoGlobal = descartarAquecimento(referencia["historicoGlobal"])
    if historicoGlobal:
        tempos = [p[0] - t0 for p in historicoGlobal] + [referencia["fim"] - t0]
        custos = [p[1] for p in historicoGlobal] + [historicoGlobal[-1][1]]
        eixo.step(tempos, custos, where="post", color=TINTA, linewidth=2.4, linestyle=(0, (4, 2)),
                  label=f"melhor global (lider: no {referencia['id']})", zorder=3)

    eixo.set_yscale("log")
    eixo.yaxis.set_major_locator(LogLocator(subs=(1.0, 2.0, 5.0)))
    eixo.yaxis.set_minor_locator(LogLocator(subs="auto"))
    eixo.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", ".")))
    eixo.yaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
    eixo.grid(axis="y", which="major", color=GRADE, linewidth=0.8)
    eixo.set_axisbelow(True)

    base, topo = eixo.get_ylim()
    topo = topo * FOLGA_ROTULOS
    eixo.set_ylim(base, topo)
    for t, marcas, textos in anotacoesDeEventos(execucao, t0):
        for tm, tipo in marcas:
            eixo.axvline(tm, color=CRITICO if tipo == "queda" else TINTA_SUAVE,
                         linestyle=":" if tipo == "queda" else "--", linewidth=1.1, zorder=1)
        eixo.annotate(" → ".join(textos), xy=(t, topo), xytext=(-4, -6), textcoords="offset points",
                      rotation=90, va="top", ha="right", fontsize=8.5, color=TINTA_SECUNDARIA, zorder=4)

    eixo.set_xlabel("tempo desde o inicio da execucao (s)", color=TINTA_SECUNDARIA, fontsize=10)
    eixo.set_ylabel("custo da melhor rota da ilha (escala log)", color=TINTA_SECUNDARIA, fontsize=10)
    eixo.set_title("Evolucao do custo em cada ilha", loc="left", fontsize=11, color=TINTA)
    eixo.legend(loc="upper right", frameon=False, fontsize=9, labelcolor=TINTA_SECUNDARIA)


def gerarFigura(referencia, execucao, caminhoSaida, mostrar):
    import matplotlib
    if not mostrar:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans", "sans-serif"]

    if referencia["melhorGlobal"]:
        melhor = referencia["melhorGlobal"]
        rota, custo, origem = melhor["rota"], melhor["custo"], melhor["origem"]
    else:
        r = min(execucao, key=lambda r: r["melhorLocal"]["custo"])
        rota, custo, origem = r["melhorLocal"]["rota"], r["melhorLocal"]["custo"], r["id"]

    t0 = min(r["inicio"] for r in execucao)
    figura, (eixoMapa, eixoCurva) = plt.subplots(1, 2, figsize=(15, 6.5), gridspec_kw={"width_ratios": [1, 1.3]})
    figura.patch.set_facecolor(SUPERFICIE)

    desenharMapa(eixoMapa, referencia["cidades"], rota, custo, origem)
    desenharEvolucao(eixoCurva, referencia, execucao, t0)

    figura.suptitle(f"Algoritmo genetico distribuido · problema {referencia['problema']} · {len(execucao)} nos com resultado",
                    x=0.01, ha="left", fontsize=13, color=TINTA)
    figura.tight_layout(rect=(0, 0, 1, 0.95))
    figura.savefig(caminhoSaida, dpi=150, facecolor=SUPERFICIE)
    print(f"imagem salva em {caminhoSaida}")

    if mostrar:
        plt.show()


#main
def main():
    parser = argparse.ArgumentParser(
        description="Gera a imagem do resultado a partir dos arquivos salvos pelos nos",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--entrada", default="resultados", help="pasta com os arquivos noX.json")
    parser.add_argument("--saida", default="resultado.png", help="arquivo de imagem gerado")
    parser.add_argument("--mostrar", action="store_true", help="tambem abre a imagem numa janela")
    args = parser.parse_args()

    resultados = carregarResultados(args.entrada)
    if not resultados:
        print(f"erro: nenhum resultado valido encontrado em '{args.entrada}'", file=sys.stderr)
        sys.exit(1)

    referencia, execucao = selecionarExecucao(resultados)
    print(f"execucao com {len(execucao)} nos | referencia: no {referencia['id']} ({'lider' if referencia['lider'] else 'nao lider'})")
    gerarFigura(referencia, execucao, args.saida, args.mostrar)


if __name__ == "__main__":
    main()
