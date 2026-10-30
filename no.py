#[imports]
import os
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

import argparse
import json
import math
import queue
import re
import sys
import threading
import time
from collections import deque
from concurrent import futures
from datetime import datetime

import grpc

import agdistribuido_pb2 as pb
import agdistribuido_pb2_grpc as pbGrpc
from ag import Ilha, gerarCidades, rotaValida, custoRota, LIMITE_CIDADES, LIMITE_POPULACAO
from eleicao import EleicaoBully
from relogio import RelogioLamport


#parametros de rede
TIMEOUT_RPC = 1.0
INTERVALO_HEARTBEAT = 1.0
FALHAS_PARA_SUSPEITAR = 2
ESPERA_COORDENADOR = 3.0
QUARENTENA_SEGUNDOS = 5.0
INTERVALO_STATUS = 3.0
ESPERA_ENCERRAMENTO = 10.0
CONTATO_RECENTE = 5.0
FILA_MIGRANTES = 20
LIMITE_HISTORICO = 20000
LIMITE_EVENTOS = 1000
OPCOES_CANAL = [
    ("grpc.initial_reconnect_backoff_ms", 500),
    ("grpc.min_reconnect_backoff_ms", 500),
    ("grpc.max_reconnect_backoff_ms", 2000),
]
TRAVA_LOG = threading.Lock()


#configuracao
TAMANHO_MAXIMO_CONFIG = 64 * 1024
LIMITE_NOS = (2, 32)
LIMITE_ID = (1, 1000)
PADRAO_ID = re.compile(r"[0-9]{1,4}")
PADRAO_ENDERECO = re.compile(r"[A-Za-z0-9.\-]+:([0-9]{1,5})")
CAMPOS_PROBLEMA = {
    "cidades": (int, *LIMITE_CIDADES),
    "semente": (int, 0, 2 ** 31 - 1),
}
CAMPOS_ALGORITMO = {
    "populacao": (int, *LIMITE_POPULACAO),
    "taxaMutacao": (float, 0.0, 1.0),
    "migrantes": (int, 1, LIMITE_POPULACAO[1] // 2),
    "intervaloMigracao": (int, 1, 100000),
    "intervaloRelatorio": (int, 1, 100000),
    "geracoesMaximas": (int, 1, 10 ** 7),
    "estagnacaoSegundos": (float, 1.0, 3600.0),
}


def lerSecao(dados, nomeSecao, campos):
    secao = dados.get(nomeSecao)
    if not isinstance(secao, dict):
        raise ValueError(f"secao '{nomeSecao}' ausente ou invalida")

    desconhecidos = set(secao) - set(campos)
    if desconhecidos:
        raise ValueError(f"campos desconhecidos em '{nomeSecao}': {sorted(desconhecidos)}")

    resultado = {}
    for nome, (tipo, minimo, maximo) in campos.items():
        if nome not in secao:
            raise ValueError(f"campo '{nomeSecao}.{nome}' ausente")
        valor = secao[nome]
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or (tipo is int and not isinstance(valor, int)):
            raise ValueError(f"campo '{nomeSecao}.{nome}' deve ser do tipo {tipo.__name__} (recebido: {valor!r})")
        if not minimo <= valor <= maximo:
            raise ValueError(f"campo '{nomeSecao}.{nome}' deve estar entre {minimo} e {maximo} (recebido: {valor})")
        resultado[nome] = tipo(valor)

    return resultado


def lerNos(dados):
    nos = dados.get("nos")
    if not isinstance(nos, dict) or not LIMITE_NOS[0] <= len(nos) <= LIMITE_NOS[1]:
        raise ValueError(f"secao 'nos' deve ter entre {LIMITE_NOS[0]} e {LIMITE_NOS[1]} nos")

    resultado = {}
    for chave, endereco in nos.items():
        if not PADRAO_ID.fullmatch(chave) or not LIMITE_ID[0] <= int(chave) <= LIMITE_ID[1]:
            raise ValueError(f"id de no invalido: '{chave}' (use inteiros entre {LIMITE_ID[0]} e {LIMITE_ID[1]})")
        combinacao = PADRAO_ENDERECO.fullmatch(endereco) if isinstance(endereco, str) else None
        if not combinacao or not 1 <= int(combinacao.group(1)) <= 65535:
            raise ValueError(f"endereco invalido para o no {chave}: {endereco!r} (formato esperado: ip:porta)")
        resultado[int(chave)] = endereco

    if len(resultado) != len(nos):
        raise ValueError("ha ids repetidos na secao 'nos'")
    if len(set(resultado.values())) != len(resultado):
        raise ValueError("ha enderecos repetidos na secao 'nos'")

    return resultado


def carregarConfiguracao(caminho):
    try:
        if os.path.getsize(caminho) > TAMANHO_MAXIMO_CONFIG:
            raise ValueError(f"arquivo de configuracao maior que {TAMANHO_MAXIMO_CONFIG // 1024} KB")
        with open(caminho, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except OSError as erro:
        raise ValueError(f"nao foi possivel abrir '{caminho}': {erro.strerror}")
    except json.JSONDecodeError as erro:
        raise ValueError(f"JSON invalido em '{caminho}': {erro}")

    if not isinstance(dados, dict):
        raise ValueError("a configuracao deve ser um objeto JSON")
    desconhecidas = set(dados) - {"problema", "algoritmo", "nos"}
    if desconhecidas:
        raise ValueError(f"secoes desconhecidas na configuracao: {sorted(desconhecidas)}")

    config = {
        "problema": lerSecao(dados, "problema", CAMPOS_PROBLEMA),
        "algoritmo": lerSecao(dados, "algoritmo", CAMPOS_ALGORITMO),
        "nos": lerNos(dados),
    }

    algoritmo = config["algoritmo"]
    if algoritmo["migrantes"] > algoritmo["populacao"] // 2:
        raise ValueError(f"'algoritmo.migrantes' nao pode passar de metade da populacao ({algoritmo['populacao'] // 2})")

    return config


#classes
class No:
    def __init__(self, idNo, config, detalhado=False, pastaSaida="resultados"):
        if idNo not in config["nos"]:
            raise ValueError(f"id {idNo} nao existe na configuracao (ids validos: {sorted(config['nos'])})")

        problema = config["problema"]
        self.idNo = idNo
        self.enderecos = config["nos"]
        self.outros = sorted(i for i in self.enderecos if i != idNo)
        self.alg = config["algoritmo"]
        self.detalhado = detalhado
        self.assinatura = f"s{problema['semente']}-c{problema['cidades']}"

        self.cidades = gerarCidades(problema["cidades"], problema["semente"])
        self.ilha = Ilha(self.cidades, tamanhoPopulacao=self.alg["populacao"], taxaMutacao=self.alg["taxaMutacao"],
                         semente=problema["semente"] * 1000 + idNo)
        self.relogio = RelogioLamport(idNo)
        self.eleicao = EleicaoBully(idNo, self.enderecos.keys(), self.enviarEleicao, self.enviarCoordenador,
                                    aoMudarLider=self.aoMudarLider, esperaCoordenador=ESPERA_COORDENADOR)

        self.canais = {i: grpc.insecure_channel(self.enderecos[i], options=OPCOES_CANAL) for i in self.outros}
        self.stubs = {i: pbGrpc.NoStub(canal) for i, canal in self.canais.items()}
        self.filaMigrantes = queue.Queue(maxsize=FILA_MIGRANTES)
        self.quarentena = {}
        self.isolado = False
        self.parar = threading.Event()
        self.servidor = None
        self.pastaSaida = pastaSaida
        self.inicio = time.time()
        self.historico = deque(maxlen=LIMITE_HISTORICO)
        self.eventos = deque(maxlen=LIMITE_EVENTOS)
        self.resumo = (0, self.ilha.melhor()[0])

        self._trava = threading.Lock()
        self._ultimoContato = {}
        self._melhorGlobal = None
        self._ultimaMelhora = time.monotonic()
        self._relatorios = {}
        self._encerrandoGlobal = False
        self._avisados = set()
        self._historicoGlobal = deque(maxlen=LIMITE_HISTORICO)

    def log(self, mensagem):
        agora = datetime.now().strftime("%H:%M:%S")
        with TRAVA_LOG:
            print(f"[{agora}][no {self.idNo}][L={self.relogio.valor:>6}] {mensagem}", flush=True)

    #comunicacao
    def novoCabecalho(self):
        return pb.Cabecalho(remetente=self.idNo, relogio=self.relogio.enviar(), problema=self.assinatura)

    def processarCabecalho(self, cabecalho, esperado=None):
        remetente = cabecalho.remetente
        if remetente not in self.enderecos or remetente == self.idNo:
            raise ValueError(f"remetente desconhecido: {remetente}")
        if esperado is not None and remetente != esperado:
            raise ValueError(f"resposta veio do no {remetente}, esperado {esperado}")
        if cabecalho.problema != self.assinatura:
            raise ValueError(f"problema diferente: {cabecalho.problema!r} (esperado {self.assinatura!r})")

        self.relogio.receber(cabecalho.relogio)
        with self._trava:
            self._ultimoContato[remetente] = time.monotonic()
        return remetente

    def chamar(self, destino, metodo, requisicao):
        try:
            resposta = getattr(self.stubs[destino], metodo)(requisicao, timeout=TIMEOUT_RPC)
            self.processarCabecalho(resposta.cabecalho, esperado=destino)
            return resposta
        except grpc.RpcError as erro:
            if self.detalhado:
                self.log(f"{metodo} -> no {destino} falhou: {erro.code().name}")
        except ValueError as erro:
            self.log(f"resposta invalida do no {destino} em {metodo}: {erro}")
        return None

    def recibo(self, aceito, motivo=""):
        return pb.Recibo(cabecalho=self.novoCabecalho(), aceito=aceito, motivo=motivo)

    #eleicao
    def enviarEleicao(self, destino):
        if self.detalhado:
            self.log(f"ELEICAO -> no {destino}")
        return self.chamar(destino, "Eleicao", pb.PedidoEleicao(cabecalho=self.novoCabecalho())) is not None

    def enviarCoordenador(self, destino):
        recibo = self.chamar(destino, "Coordenador", pb.AnuncioCoordenador(cabecalho=self.novoCabecalho()))
        return recibo is not None and recibo.aceito

    def aoMudarLider(self, anterior, novo):
        if novo is None:
            self.eventos.append([time.time(), "queda", anterior])
            self.log(f"lider {anterior} considerado MORTO -> sem lider")
        elif novo == self.idNo:
            with self._trava:
                self._melhorGlobal = None
                self._ultimaMelhora = time.monotonic()
                self._relatorios = {}
                self._encerrandoGlobal = False
                self._avisados = set()
                self._historicoGlobal.clear()
            self.eventos.append([time.time(), "lideranca", self.idNo])
            self.log("*** ASSUMI A LIDERANCA ***")
        else:
            self.eventos.append([time.time(), "lideranca", novo])
            self.log(f"novo lider: no {novo}")

    #migracao
    def sucessores(self):
        ids = sorted(self.enderecos)
        posicao = ids.index(self.idNo)
        return ids[posicao + 1:] + ids[:posicao]

    def migrar(self):
        rotas = self.ilha.escolherMigrantes(self.alg["migrantes"])

        for destino in self.sucessores():
            if self.quarentena.get(destino, 0) > time.monotonic():
                continue

            pacote = pb.PacoteMigrantes(cabecalho=self.novoCabecalho(), geracao=self.ilha.geracao,
                                        rotas=[pb.Rota(cidades=r) for r in rotas])
            recibo = self.chamar(destino, "EnviarMigrantes", pacote)

            if recibo is None:
                self.quarentena[destino] = time.monotonic() + QUARENTENA_SEGUNDOS
                self.log(f"no {destino} nao respondeu a migracao -> quarentena de {QUARENTENA_SEGUNDOS:.0f}s, tentando o proximo do anel")
                continue

            self.quarentena.pop(destino, None)
            if self.isolado:
                self.isolado = False
                self.log(f"migracao restabelecida com o no {destino}")
            if not recibo.aceito:
                self.log(f"no {destino} recusou migrantes: {recibo.motivo}")
            elif self.detalhado:
                self.log(f"MIGRANTES -> no {destino} ({len(rotas)} rotas)")
            return

        if not self.isolado:
            self.isolado = True
            self.log("nenhum vizinho disponivel para migracao -> evoluindo isolado")

    def receberMigrantes(self, remetente, requisicao):
        rotas = [list(r.cidades) for r in requisicao.rotas]
        if not rotas or len(rotas) > self.ilha.maximoMigrantes:
            return self.recibo(False, f"quantidade de migrantes invalida: {len(rotas)}")
        if not all(rotaValida(r, self.ilha.n) for r in rotas):
            return self.recibo(False, "pacote contem rota invalida")

        try:
            self.filaMigrantes.put_nowait(rotas)
        except queue.Full:
            return self.recibo(False, "fila de migrantes cheia")

        if self.detalhado:
            self.log(f"MIGRANTES <- no {remetente} ({len(rotas)} rotas, L do remetente={requisicao.cabecalho.relogio})")
        return self.recibo(True)

    def absorverMigrantes(self):
        while True:
            try:
                rotas = self.filaMigrantes.get_nowait()
            except queue.Empty:
                return
            self.ilha.receberMigrantes(rotas)

    #lideranca
    def reportar(self):
        custo, rota = self.ilha.melhor()
        lider = self.eleicao.lider

        if lider is None:
            return
        if lider == self.idNo:
            self.registrarRelatorio(self.idNo, self.ilha.geracao, custo, rota)
            return

        relatorio = pb.RelatorioIlha(cabecalho=self.novoCabecalho(), geracao=self.ilha.geracao,
                                     custo=custo, rota=pb.Rota(cidades=rota))
        resposta = self.chamar(lider, "ReportarMelhor", relatorio)
        if resposta is not None and resposta.encerrar:
            self.log(f"lider {lider} mandou ENCERRAR")
            self.parar.set()

    def registrarRelatorio(self, origem, geracao, custo, rota):
        with self._trava:
            self._relatorios[origem] = (geracao, custo)
            if self._melhorGlobal is None or custo < self._melhorGlobal[0] - 1e-9:
                self._melhorGlobal = (custo, list(rota), origem)
                self._ultimaMelhora = time.monotonic()
                self._historicoGlobal.append([time.time(), custo, origem])
            if self._encerrandoGlobal:
                self._avisados.add(origem)
            return self._encerrandoGlobal

    def motivoParaEncerrar(self):
        if self.ilha.geracao >= self.alg["geracoesMaximas"]:
            return f"limite de {self.alg['geracoesMaximas']} geracoes atingido"
        with self._trava:
            parado = time.monotonic() - self._ultimaMelhora
        if parado >= self.alg["estagnacaoSegundos"]:
            return f"melhor global sem melhora ha {parado:.0f}s"
        return None

    def encerrarSistema(self, motivo):
        with self._trava:
            self._encerrandoGlobal = True
            self._avisados.add(self.idNo)
        self.log(f"=== LIDER DECIDIU ENCERRAR: {motivo} ===")

        limite = time.monotonic() + ESPERA_ENCERRAMENTO
        while time.monotonic() < limite:
            with self._trava:
                agora = time.monotonic()
                pendentes = [i for i in self.outros
                             if i not in self._avisados and agora - self._ultimoContato.get(i, -math.inf) < CONTATO_RECENTE]
            if not pendentes:
                return
            time.sleep(0.2)
        self.log(f"tempo de espera esgotado; nos nao avisados: {pendentes}")

    def estadoAtual(self):
        geracao, custo = self.resumo
        with self._trava:
            melhorGlobal = self._melhorGlobal if self.eleicao.souLider else None

        return pb.EstadoNo(
            id=self.idNo, problema=self.assinatura, relogio=self.relogio.valor,
            lider=self.eleicao.lider or 0, emEleicao=self.eleicao.emEleicao, isolado=self.isolado,
            geracao=geracao, melhorLocal=custo,
            melhorGlobal=melhorGlobal[0] if melhorGlobal else 0.0,
            origemMelhorGlobal=melhorGlobal[2] if melhorGlobal else 0,
        )

    #execucao
    def iniciarServidor(self):
        endereco = self.enderecos[self.idNo]
        self.servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
        pbGrpc.add_NoServicer_to_server(ServicoNo(self), self.servidor)

        try:
            porta = self.servidor.add_insecure_port(endereco)
        except RuntimeError:
            porta = 0
        if porta == 0:
            raise RuntimeError(f"nao foi possivel abrir {endereco} (porta em uso ou IP nao pertence a esta maquina)")

        self.servidor.start()

    def loopHeartbeat(self):
        falhas = 0
        while not self.parar.wait(INTERVALO_HEARTBEAT):
            lider = self.eleicao.lider

            if lider is None:
                falhas = 0
                if not self.eleicao.emEleicao:
                    self.eleicao.iniciarEleicao()
                continue
            if lider == self.idNo:
                falhas = 0
                continue

            pong = self.chamar(lider, "Heartbeat", pb.Ping(cabecalho=self.novoCabecalho()))
            if pong is not None:
                falhas = 0
                if pong.lider != lider:
                    self.log(f"no {lider} diz que o lider e {pong.lider or 'nenhum'} -> visao divergente, refazendo ELEICAO")
                self.eleicao.verificarLider(pong.lider or None)
                continue

            falhas += 1
            self.log(f"heartbeat ao lider {lider} falhou ({falhas}/{FALHAS_PARA_SUSPEITAR})")
            if falhas >= FALHAS_PARA_SUSPEITAR:
                falhas = 0
                self.log(f"lider {lider} nao responde -> iniciando ELEICAO")
                self.eleicao.liderFalhou(lider)

    def mostrarStatus(self):
        lider = self.eleicao.lider
        texto = f"geracao {self.ilha.geracao:>6} | melhor local {self.ilha.melhor()[0]:9.2f} | lider: {lider if lider else '?'}"

        if self.eleicao.souLider:
            with self._trava:
                melhorGlobal = self._melhorGlobal
                reportando = sorted(self._relatorios)
            if melhorGlobal:
                texto += f" | MELHOR GLOBAL {melhorGlobal[0]:.2f} (no {melhorGlobal[2]}) | ilhas reportando: {reportando}"

        self.log(texto)

    def executar(self):
        self.iniciarServidor()
        self.log(f"ouvindo em {self.enderecos[self.idNo]} | problema {self.assinatura} | nos: {sorted(self.enderecos)}")
        threading.Thread(target=self.loopHeartbeat, daemon=True).start()
        self.eleicao.iniciarEleicao()

        melhorLocal = self.ilha.melhor()[0]
        proximoStatus = time.monotonic() + INTERVALO_STATUS

        try:
            while not self.parar.is_set():
                self.absorverMigrantes()
                self.ilha.proximaGeracao()
                geracao = self.ilha.geracao
                self.resumo = (geracao, self.ilha.melhor()[0])

                if self.ilha.melhor()[0] < melhorLocal - 1e-9:
                    melhorLocal = self.ilha.melhor()[0]
                    self.relogio.tick()
                if geracao % self.alg["intervaloMigracao"] == 0:
                    self.migrar()
                if geracao % self.alg["intervaloRelatorio"] == 0:
                    self.historico.append([time.time(), geracao, self.ilha.melhor()[0]])
                    self.reportar()
                if time.monotonic() >= proximoStatus:
                    self.mostrarStatus()
                    proximoStatus = time.monotonic() + INTERVALO_STATUS

                if self.eleicao.souLider:
                    motivo = self.motivoParaEncerrar()
                    if motivo:
                        self.encerrarSistema(motivo)
                        break
                elif geracao >= self.alg["geracoesMaximas"]:
                    self.log("limite de geracoes atingido")
                    self.reportar()
                    break
        except KeyboardInterrupt:
            self.log("interrompido pelo usuario (Ctrl+C)")
        finally:
            self.finalizar()

    def finalizar(self):
        self.parar.set()
        souLider = self.eleicao.souLider
        self.eleicao.encerrar()

        custo, _ = self.ilha.melhor()
        self.log(f"FIM | geracoes: {self.ilha.geracao} | melhor local: {custo:.2f}")

        if souLider:
            with self._trava:
                melhorGlobal = self._melhorGlobal
                relatorios = dict(self._relatorios)
            if melhorGlobal:
                self.log("ultimo relatorio de cada ilha:")
                for origem, (geracao, custoIlha) in sorted(relatorios.items()):
                    self.log(f"    no {origem}: geracao {geracao:>6} | custo {custoIlha:9.2f}")
                self.log(f"MELHOR ROTA GLOBAL: custo {melhorGlobal[0]:.2f} (encontrada pelo no {melhorGlobal[2]})")
                self.log(f"rota: {melhorGlobal[1]}")

        self.salvarResultado(souLider)

        if self.servidor:
            self.servidor.stop(grace=1).wait()
        for canal in self.canais.values():
            canal.close()

    def salvarResultado(self, souLider):
        custo, rota = self.ilha.melhor()
        with self._trava:
            melhorGlobal = self._melhorGlobal
            historicoGlobal = list(self._historicoGlobal)

        resultado = {
            "id": self.idNo,
            "problema": self.assinatura,
            "cidades": self.cidades,
            "inicio": self.inicio,
            "fim": time.time(),
            "lider": souLider,
            "melhorLocal": {"custo": custo, "rota": rota},
            "melhorGlobal": {"custo": melhorGlobal[0], "rota": melhorGlobal[1], "origem": melhorGlobal[2]} if souLider and melhorGlobal else None,
            "historico": list(self.historico),
            "historicoGlobal": historicoGlobal if souLider else [],
            "eventos": list(self.eventos),
        }

        caminho = os.path.join(self.pastaSaida, f"no{self.idNo}.json")
        try:
            os.makedirs(self.pastaSaida, exist_ok=True)
            with open(caminho + ".tmp", "w", encoding="utf-8") as arquivo:
                json.dump(resultado, arquivo)
            os.replace(caminho + ".tmp", caminho)
            self.log(f"resultado salvo em {caminho}")
        except OSError as erro:
            self.log(f"nao foi possivel salvar o resultado em {caminho}: {erro.strerror}")


class ServicoNo(pbGrpc.NoServicer):
    def __init__(self, no):
        self.no = no

    def validar(self, requisicao, contexto):
        try:
            return self.no.processarCabecalho(requisicao.cabecalho)
        except ValueError as erro:
            self.no.log(f"mensagem recusada de {contexto.peer()}: {erro}")
            contexto.abort(grpc.StatusCode.INVALID_ARGUMENT, str(erro))

    def Heartbeat(self, requisicao, contexto):
        self.validar(requisicao, contexto)
        return pb.Pong(cabecalho=self.no.novoCabecalho(), lider=self.no.eleicao.lider or 0)

    def EnviarMigrantes(self, requisicao, contexto):
        remetente = self.validar(requisicao, contexto)
        return self.no.receberMigrantes(remetente, requisicao)

    def Eleicao(self, requisicao, contexto):
        remetente = self.validar(requisicao, contexto)
        if self.no.detalhado:
            self.no.log(f"ELEICAO <- no {remetente}")
        if not self.no.eleicao.receberEleicao(remetente):
            contexto.abort(grpc.StatusCode.INVALID_ARGUMENT, "eleicao so pode vir de id menor")
        return pb.RespostaEleicao(cabecalho=self.no.novoCabecalho())

    def Coordenador(self, requisicao, contexto):
        remetente = self.validar(requisicao, contexto)
        aceito = self.no.eleicao.receberCoordenador(remetente)
        if not aceito:
            self.no.log(f"anuncio de coordenador do no {remetente} recusado (id menor que o meu)")
        return self.no.recibo(aceito)

    def ReportarMelhor(self, requisicao, contexto):
        remetente = self.validar(requisicao, contexto)
        if not self.no.eleicao.souLider:
            contexto.abort(grpc.StatusCode.FAILED_PRECONDITION, "este no nao e o lider")

        rota = list(requisicao.rota.cidades)
        if not rotaValida(rota, self.no.ilha.n):
            self.no.log(f"relatorio do no {remetente} recusado: rota invalida")
            contexto.abort(grpc.StatusCode.INVALID_ARGUMENT, "rota invalida")

        custoReal = custoRota(rota, self.no.ilha.matriz)
        if abs(custoReal - requisicao.custo) > 1e-6 * max(1.0, custoReal):
            self.no.log(f"relatorio do no {remetente} recusado: custo informado {requisicao.custo:.2f}, custo real {custoReal:.2f}")
            contexto.abort(grpc.StatusCode.INVALID_ARGUMENT, "custo informado nao confere com a rota")

        encerrar = self.no.registrarRelatorio(remetente, requisicao.geracao, custoReal, rota)
        return pb.RespostaRelatorio(cabecalho=self.no.novoCabecalho(), encerrar=encerrar)

    def ObterEstado(self, requisicao, contexto):
        return self.no.estadoAtual()


#main
def main():
    parser = argparse.ArgumentParser(
        description="No do algoritmo genetico distribuido (TSP em ilhas)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--id", type=int, required=True, help="id deste no (precisa existir na configuracao)")
    parser.add_argument("--config", default="nos_local.json", help="arquivo JSON com problema, algoritmo e nos")
    parser.add_argument("--detalhado", action="store_true", help="mostra cada mensagem de migracao e eleicao")
    parser.add_argument("--saida", default="resultados", help="pasta onde o resultado deste no e salvo ao terminar")
    args = parser.parse_args()

    try:
        no = No(args.id, carregarConfiguracao(args.config), args.detalhado, args.saida)
    except ValueError as erro:
        parser.error(str(erro))

    try:
        no.executar()
    except RuntimeError as erro:
        print(f"erro: {erro}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
