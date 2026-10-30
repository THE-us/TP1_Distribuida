"""
name: THE-us
file: eleicao.py
description: Eleicao de lider pelo algoritmo Bully, independente da camada de comunicacao
"""

#[imports]
import threading
from concurrent.futures import ThreadPoolExecutor


#funcoes
def tentarEnvio(funcao, destino):
    try:
        return bool(funcao(destino))
    except Exception:
        return False


#classes
class EleicaoBully:
    def __init__(self, idNo, idsNos, enviarEleicao, enviarCoordenador, aoMudarLider=None, esperaCoordenador=3.0):
        if idNo not in idsNos:
            raise ValueError(f"id {idNo} nao pertence aos nos conhecidos {sorted(idsNos)}")

        self.idNo = idNo
        self.idsNos = frozenset(idsNos)
        self.maiores = sorted(i for i in self.idsNos if i > idNo)
        self.outros = sorted(i for i in self.idsNos if i != idNo)
        self.enviarEleicao = enviarEleicao
        self.enviarCoordenador = enviarCoordenador
        self.aoMudarLider = aoMudarLider
        self.esperaCoordenador = esperaCoordenador

        self._lider = None
        self._emEleicao = False
        self._reeleicaoPendente = False
        self._trava = threading.Lock()
        self._coordenadorChegou = threading.Event()
        self._encerrado = threading.Event()

    @property
    def lider(self):
        with self._trava:
            return self._lider

    @property
    def souLider(self):
        return self.lider == self.idNo

    @property
    def emEleicao(self):
        with self._trava:
            return self._emEleicao

    def iniciarEleicao(self):
        with self._trava:
            if self._emEleicao or self._encerrado.is_set():
                return False
            self._emEleicao = True
            self._coordenadorChegou.clear()

        threading.Thread(target=self._conduzirEleicao, daemon=True).start()
        return True

    def liderFalhou(self, idSuspeito):
        with self._trava:
            if self._lider != idSuspeito:
                return
        self._definirLider(None)
        self.iniciarEleicao()

    def verificarLider(self, liderInformado):
        lider = self.lider
        if lider is not None and lider != self.idNo and liderInformado != lider:
            self._agendarReeleicao()

    def receberEleicao(self, remetente):
        if remetente not in self.idsNos or remetente >= self.idNo:
            return False
        self.iniciarEleicao()
        return True

    def receberCoordenador(self, remetente):
        if remetente not in self.idsNos:
            return False
        if remetente < self.idNo:
            self._agendarReeleicao()
            return False

        self._definirLider(remetente, coordenadorChegou=True)
        return True

    def encerrar(self):
        self._encerrado.set()
        self._coordenadorChegou.set()

    def _agendarReeleicao(self):
        with self._trava:
            if self._emEleicao:
                self._reeleicaoPendente = True
                return
        self.iniciarEleicao()

    def _conduzirEleicao(self):
        try:
            while True:
                self._rodarEleicao()
                with self._trava:
                    if not self._reeleicaoPendente or self._encerrado.is_set():
                        return
                    self._reeleicaoPendente = False
                    self._coordenadorChegou.clear()
        finally:
            with self._trava:
                self._emEleicao = False
                self._reeleicaoPendente = False

    def _rodarEleicao(self):
        while not self._encerrado.is_set():
            if not self._algumMaiorRespondeu():
                self._assumirLideranca()
                return
            if self._coordenadorChegou.wait(self.esperaCoordenador):
                return

    def _algumMaiorRespondeu(self):
        if not self.maiores:
            return False
        with ThreadPoolExecutor(max_workers=len(self.maiores)) as pool:
            respostas = list(pool.map(lambda d: tentarEnvio(self.enviarEleicao, d), self.maiores))
        return any(respostas)

    def _assumirLideranca(self):
        if not self._definirLider(self.idNo, somenteSemCoordenadorMaior=True):
            return
        if not self.outros:
            return
        with ThreadPoolExecutor(max_workers=len(self.outros)) as pool:
            list(pool.map(lambda d: tentarEnvio(self.enviarCoordenador, d), self.outros))

    def _definirLider(self, novoLider, coordenadorChegou=False, somenteSemCoordenadorMaior=False):
        with self._trava:
            if somenteSemCoordenadorMaior and (self._encerrado.is_set() or self._coordenadorChegou.is_set()):
                return False
            anterior = self._lider
            self._lider = novoLider
            if coordenadorChegou:
                self._coordenadorChegou.set()

        if anterior != novoLider and self.aoMudarLider:
            self.aoMudarLider(anterior, novoLider)
        return True
