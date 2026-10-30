"""
name: THE-us
file: painel.py
description: Painel interativo para iniciar, derrubar e acompanhar os nos do algoritmo genetico distribuido
"""

#[imports]
import os
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import grpc

import agdistribuido_pb2 as pb
import agdistribuido_pb2_grpc as pbGrpc
from ag import LIMITE_CIDADES, LIMITE_POPULACAO
from no import OPCOES_CANAL, carregarConfiguracao
from simulacao_local import LIMITE_GERACOES, LIMITE_ILHAS


#parametros
PASTA = os.path.dirname(os.path.abspath(__file__))
NO_WINDOWS = os.name == "nt"
CONFIGURACOES = {1: "nos_local.json", 2: "nos_lan.json", 3: "config_personalizada.json"}
PADRAO_NOME_CONFIG = re.compile(r"[A-Za-z0-9_]+\.json")
HOSTS_LOCAIS = {"127.0.0.1", "localhost"}
TIMEOUT_ESTADO = 0.8
INTERVALO_AO_VIVO = 2.0
INTERVALO_ENTRE_NOS = 0.4
LIMITE_NOS_PERSONALIZADO = (2, 10)
LIMITE_ESTAGNACAO = (5, 3600)
PORTA_BASE = 50051


#entrada
def lerInteiro(mensagem, minimo, maximo, padrao):
    while True:
        texto = input(f"{mensagem} ({minimo} a {maximo}, Enter = {padrao}): ").strip()
        if not texto:
            return padrao
        try:
            valor = int(texto)
        except ValueError:
            print("  digite um numero inteiro")
            continue
        if minimo <= valor <= maximo:
            return valor
        print(f"  valor fora da faixa permitida ({minimo} a {maximo})")


def lerIdNo(ids):
    validos = sorted(ids)
    while True:
        texto = input(f"Qual no? {validos}: ").strip()
        if texto.isdigit() and int(texto) in ids:
            return int(texto)
        print(f"  escolha um destes: {validos}")


def confirmar(mensagem, padrao=True):
    texto = input(f"{mensagem} (s/n, Enter = {'s' if padrao else 'n'}): ").strip().lower()
    return padrao if not texto else texto.startswith("s")


def limparTela():
    os.system("cls" if NO_WINDOWS else "clear")


def pausar():
    input("\nEnter para voltar ao menu...")


#classes
class Painel:
    def __init__(self, nomeConfig):
        self.processos = {}
        self.canais = {}
        self.usarConfiguracao(nomeConfig)

    #configuracao
    def usarConfiguracao(self, nomeConfig):
        if not PADRAO_NOME_CONFIG.fullmatch(nomeConfig):
            raise ValueError(f"nome de configuracao invalido: {nomeConfig!r}")

        config = carregarConfiguracao(os.path.join(PASTA, nomeConfig))
        for canal in self.canais.values():
            canal.close()

        self.nomeConfig = nomeConfig
        self.config = config
        self.assinatura = f"s{config['problema']['semente']}-c{config['problema']['cidades']}"
        self.canais = {i: grpc.insecure_channel(e, options=OPCOES_CANAL) for i, e in config["nos"].items()}
        self.stubs = {i: pbGrpc.NoStub(c) for i, c in self.canais.items()}

    def configuracaoLocal(self):
        return all(e.rsplit(":", 1)[0] in HOSTS_LOCAIS for e in self.config["nos"].values())

    def criarConfiguracaoPersonalizada(self):
        print("\nNova configuracao local (todos os nos neste PC):")
        quantidade = lerInteiro("Quantidade de nos", *LIMITE_NOS_PERSONALIZADO, 5)
        cidades = lerInteiro("Quantidade de cidades", *LIMITE_CIDADES, 200)
        populacao = lerInteiro("Individuos por ilha", *LIMITE_POPULACAO, 150)
        estagnacao = lerInteiro("Encerrar apos quantos segundos sem melhora", *LIMITE_ESTAGNACAO, 60)
        semente = lerInteiro("Semente do mapa de cidades", 0, 2 ** 31 - 1, 42)

        config = {
            "problema": {"cidades": cidades, "semente": semente},
            "algoritmo": {
                "populacao": populacao,
                "taxaMutacao": 0.3,
                "migrantes": min(3, populacao // 2),
                "intervaloMigracao": 25,
                "intervaloRelatorio": 25,
                "geracoesMaximas": 100000,
                "estagnacaoSegundos": estagnacao,
            },
            "nos": {str(i): f"127.0.0.1:{PORTA_BASE + i - 1}" for i in range(1, quantidade + 1)},
        }

        with open(os.path.join(PASTA, CONFIGURACOES[3]), "w", encoding="utf-8") as arquivo:
            json.dump(config, arquivo, indent=2)
        return CONFIGURACOES[3]

    #processos
    def processoAberto(self, idNo):
        processo = self.processos.get(idNo)
        return processo is not None and processo.poll() is None

    def abertosPeloPainel(self):
        return [i for i in sorted(self.processos) if self.processoAberto(i)]

    def iniciarNo(self, idNo):
        if self.processoAberto(idNo):
            if self.consultar(idNo) is not None:
                return f"no {idNo} ja esta rodando"
            self.fecharProcesso(idNo)

        if NO_WINDOWS:
            comando = f'cmd /k "title No {idNo} & "{sys.executable}" no.py --id {idNo} --config "{self.nomeConfig}""'
            processo = subprocess.Popen(comando, cwd=PASTA, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            os.makedirs(os.path.join(PASTA, "resultados"), exist_ok=True)
            with open(os.path.join(PASTA, "resultados", f"no{idNo}.log"), "w") as log:
                processo = subprocess.Popen([sys.executable, "no.py", "--id", str(idNo), "--config", self.nomeConfig],
                                            cwd=PASTA, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

        self.processos[idNo] = processo
        destino = "numa nova janela" if NO_WINDOWS else f"(log em resultados/no{idNo}.log)"
        return f"no {idNo} iniciado {destino}"

    def fecharProcesso(self, idNo):
        processo = self.processos.pop(idNo)
        if NO_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(processo.pid), "/T", "/F"], capture_output=True)
        else:
            processo.kill()
        processo.wait(timeout=5)

    def derrubarNo(self, idNo):
        if not self.processoAberto(idNo):
            return f"no {idNo} nao foi aberto por este painel (ou ja foi fechado)"
        self.fecharProcesso(idNo)
        return f"no {idNo} DERRUBADO (processo encerrado a forca, como um PC desligando)"

    #estado
    def consultar(self, idNo):
        try:
            estado = self.stubs[idNo].ObterEstado(pb.PedidoEstado(), timeout=TIMEOUT_ESTADO)
        except grpc.RpcError:
            return None
        if estado.id != idNo or estado.problema != self.assinatura:
            return "inconsistente"
        return estado

    def consultarTodos(self):
        ids = sorted(self.config["nos"])
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            return dict(zip(ids, pool.map(self.consultar, ids)))

    def mostrarEstado(self):
        estados = self.consultarTodos()
        print(f"{'no':>3}  {'endereco':<21} {'janela':<8} {'situacao':<14} {'lider':>5} {'geracao':>8} {'melhor local':>13} {'Lamport':>8}")
        print("-" * 88)

        for idNo, estado in estados.items():
            janela = "aberta" if self.processoAberto(idNo) else "-"
            inicio = f"{idNo:>3}  {self.config['nos'][idNo]:<21} {janela:<8}"
            if estado is None:
                print(f"{inicio} {'SEM RESPOSTA':<14}")
            elif estado == "inconsistente":
                print(f"{inicio} {'INCONSISTENTE':<14} (outro problema ou id trocado)")
            else:
                situacao = "LIDER" if estado.lider == idNo else "em eleicao" if estado.emEleicao else "isolado" if estado.isolado else "ativo"
                lider = estado.lider if estado.lider else "?"
                print(f"{inicio} {situacao:<14} {lider:>5} {estado.geracao:>8} {estado.melhorLocal:>13.2f} {estado.relogio:>8}")

        vivos = {i: e for i, e in estados.items() if e is not None and e != "inconsistente"}
        print()
        if not vivos:
            print("Nenhum no respondeu.")
            return

        visoes = {e.lider for e in vivos.values()}
        if len(visoes) == 1 and 0 not in visoes:
            print(f"Consenso: os {len(vivos)} nos ativos concordam que o lider e o no {visoes.pop()}.")
        else:
            print(f"Sem consenso ainda (eleicao em andamento?): {{no: lider que ele ve}} = { {i: e.lider or '?' for i, e in vivos.items()} }")

        lideres = [e for i, e in vivos.items() if e.lider == i and e.melhorGlobal > 0]
        if lideres:
            print(f"Melhor global (segundo o lider): {lideres[0].melhorGlobal:.2f}, encontrado pelo no {lideres[0].origemMelhorGlobal}")

    def acompanharAoVivo(self):
        try:
            while True:
                limparTela()
                print(f"AO VIVO | {time.strftime('%H:%M:%S')} | configuracao {self.nomeConfig} | Ctrl+C para voltar ao menu\n")
                self.mostrarEstado()
                time.sleep(INTERVALO_AO_VIVO)
        except KeyboardInterrupt:
            pass

    #acoes do menu
    def acaoIniciarTodos(self):
        if not self.configuracaoLocal():
            print("Esta configuracao usa varias maquinas: inicie apenas o no DESTA maquina (opcao 4).")
            return
        for idNo in sorted(self.config["nos"]):
            print(self.iniciarNo(idNo))
            time.sleep(INTERVALO_ENTRE_NOS)
        print("\nDica: use a opcao 2 para acompanhar a eleicao e a evolucao ao vivo.")

    def acaoIniciarUm(self):
        print(self.iniciarNo(lerIdNo(self.config["nos"])))

    def acaoDerrubarUm(self):
        abertos = self.abertosPeloPainel()
        if not abertos:
            print("Nenhum no foi aberto por este painel.")
            return
        print(f"Nos abertos por este painel: {abertos}")
        print(self.derrubarNo(lerIdNo(set(abertos))))

    def acaoDerrubarTodos(self):
        abertos = self.abertosPeloPainel()
        if not abertos:
            print("Nenhum no foi aberto por este painel.")
            return
        for idNo in abertos:
            print(self.derrubarNo(idNo))

    def acaoConfiguracao(self):
        abertos = self.abertosPeloPainel()
        if abertos:
            print(f"Ha nos rodando com a configuracao atual ({abertos}). Derrube-os antes de trocar (opcao 6).")
            return

        print("[1] nos_local.json  - 5 nos neste PC")
        print("[2] nos_lan.json    - 5 maquinas na rede (edite os IPs antes)")
        print("[3] personalizada   - voce escolhe nos, cidades, populacao...")
        escolha = lerInteiro("Opcao", 1, 3, 1)

        nome = self.criarConfiguracaoPersonalizada() if escolha == 3 else CONFIGURACOES[escolha]
        try:
            self.usarConfiguracao(nome)
            print(f"\nConfiguracao em uso: {nome}")
        except ValueError as erro:
            print(f"\nConfiguracao recusada: {erro}")

    def acaoSimulacaoLocal(self):
        print("\nSimulacao local: ilhas isoladas x ilhas com migracao (sem rede)")
        cidades = lerInteiro("Quantidade de cidades", *LIMITE_CIDADES, 150)
        ilhas = lerInteiro("Quantidade de ilhas", *LIMITE_ILHAS, 4)
        geracoes = lerInteiro("Quantidade de geracoes", *LIMITE_GERACOES, 800)
        populacao = lerInteiro("Individuos por ilha", *LIMITE_POPULACAO, 100)
        intervalo = lerInteiro("Migrar a cada quantas geracoes", 1, geracoes, min(20, geracoes))
        migrantes = lerInteiro("Migrantes por vez", 1, populacao // 2, min(3, populacao // 2))

        print()
        try:
            subprocess.run([sys.executable, "simulacao_local.py", "--cidades", str(cidades), "--ilhas", str(ilhas),
                            "--geracoes", str(geracoes), "--populacao", str(populacao),
                            "--intervalo", str(intervalo), "--migrantes", str(migrantes)], cwd=PASTA)
        except KeyboardInterrupt:
            pass

    def acaoGerarImagem(self):
        resultado = subprocess.run([sys.executable, "visualizar.py"], cwd=PASTA)
        caminho = os.path.join(PASTA, "resultado.png")
        if resultado.returncode == 0 and os.path.exists(caminho):
            if NO_WINDOWS:
                os.startfile(caminho)
            else:
                print(f"abra o arquivo {caminho}")

    def sair(self):
        abertos = self.abertosPeloPainel()
        if abertos and confirmar(f"Derrubar os nos abertos por este painel {abertos} antes de sair?"):
            self.acaoDerrubarTodos()
        for canal in self.canais.values():
            canal.close()


#main
def main():
    try:
        painel = Painel(CONFIGURACOES[1])
    except ValueError as erro:
        print(f"erro ao carregar {CONFIGURACOES[1]}: {erro}")
        sys.exit(1)

    acoes = {
        1: ("Ver estado dos nos", painel.mostrarEstado),
        2: ("Acompanhar ao vivo", painel.acompanharAoVivo),
        3: ("Iniciar todos os nos", painel.acaoIniciarTodos),
        4: ("Iniciar um no (ou religar um derrubado)", painel.acaoIniciarUm),
        5: ("Derrubar um no (simula falha)", painel.acaoDerrubarUm),
        6: ("Derrubar todos os nos", painel.acaoDerrubarTodos),
        7: ("Escolher ou criar configuracao", painel.acaoConfiguracao),
        8: ("Simulacao local, sem rede", painel.acaoSimulacaoLocal),
        9: ("Gerar e abrir a imagem do resultado", painel.acaoGerarImagem),
    }

    try:
        while True:
            limparTela()
            problema, algoritmo = painel.config["problema"], painel.config["algoritmo"]
            print("=== PAINEL DO ALGORITMO GENETICO DISTRIBUIDO ===")
            print(f"Configuracao: {painel.nomeConfig} | {len(painel.config['nos'])} nos | {problema['cidades']} cidades | "
                  f"populacao {algoritmo['populacao']} | encerra apos {algoritmo['estagnacaoSegundos']:.0f}s sem melhora")
            print(f"Nos abertos por este painel: {painel.abertosPeloPainel() or 'nenhum'}\n")

            for numero, (descricao, _) in acoes.items():
                print(f"[{numero}] {descricao}")
            print("[0] Sair")

            opcao = lerInteiro("\nOpcao", 0, len(acoes), 1)
            if opcao == 0:
                break

            print()
            acoes[opcao][1]()
            if opcao != 2:
                pausar()
    except (KeyboardInterrupt, EOFError):
        print()

    painel.sair()


if __name__ == "__main__":
    main()
