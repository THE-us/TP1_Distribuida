#[imports]
import math
import random


#limites
LIMITE_CIDADES = (5, 1000)
LIMITE_POPULACAO = (10, 5000)


#funcoes
def validarFaixa(nome, valor, minimo, maximo):
    if not minimo <= valor <= maximo:
        raise ValueError(f"{nome} deve estar entre {minimo} e {maximo} (recebido: {valor})")


def gerarCidades(quantidade, semente):
    validarFaixa("quantidade de cidades", quantidade, *LIMITE_CIDADES)
    gerador = random.Random(semente)
    return [(gerador.uniform(0, 100), gerador.uniform(0, 100)) for _ in range(quantidade)]


def matrizDistancias(cidades):
    return [[math.hypot(a[0] - b[0], a[1] - b[1]) for b in cidades] for a in cidades]


def custoRota(rota, matriz):
    return sum(matriz[rota[i - 1]][rota[i]] for i in range(len(rota)))


def rotaValida(rota, n):
    try:
        return len(rota) == n and set(rota) == set(range(n))
    except TypeError:
        return False


def rotaAleatoria(n, gerador):
    rota = list(range(n))
    gerador.shuffle(rota)
    return rota


def cruzamentoOX(pai, mae, gerador):
    n = len(pai)
    ini, fim = sorted(gerador.sample(range(n), 2))

    filho = [None] * n
    filho[ini:fim + 1] = pai[ini:fim + 1]
    usados = set(filho[ini:fim + 1])
    resto = [c for c in mae if c not in usados]

    pos = 0
    for i in range(n):
        if filho[i] is None:
            filho[i] = resto[pos]
            pos += 1

    return filho


def mutacaoInversao(rota, gerador):
    ini, fim = sorted(gerador.sample(range(len(rota)), 2))
    rota[ini:fim + 1] = reversed(rota[ini:fim + 1])


#classes
class Ilha:
    def __init__(self, cidades, tamanhoPopulacao=100, taxaMutacao=0.3, tamanhoTorneio=3, elite=2, semente=None):
        validarFaixa("quantidade de cidades", len(cidades), *LIMITE_CIDADES)
        validarFaixa("tamanho da populacao", tamanhoPopulacao, *LIMITE_POPULACAO)
        validarFaixa("taxa de mutacao", taxaMutacao, 0.0, 1.0)
        validarFaixa("tamanho do torneio", tamanhoTorneio, 2, tamanhoPopulacao)
        validarFaixa("elite", elite, 0, tamanhoPopulacao // 2)

        self.n = len(cidades)
        self.matriz = matrizDistancias(cidades)
        self.taxaMutacao = taxaMutacao
        self.tamanhoTorneio = tamanhoTorneio
        self.elite = elite
        self.gerador = random.Random(semente)
        self.geracao = 0

        self.populacao = [self.avaliar(rotaAleatoria(self.n, self.gerador)) for _ in range(tamanhoPopulacao)]
        self.ordenar()

    @property
    def maximoMigrantes(self):
        return len(self.populacao) // 2

    def avaliar(self, rota):
        return (custoRota(rota, self.matriz), rota)

    def ordenar(self):
        self.populacao.sort(key=lambda ind: ind[0])

    def torneio(self):
        competidores = self.gerador.sample(self.populacao, self.tamanhoTorneio)
        return min(competidores, key=lambda ind: ind[0])[1]

    def proximaGeracao(self):
        nova = self.populacao[:self.elite]

        while len(nova) < len(self.populacao):
            filho = cruzamentoOX(self.torneio(), self.torneio(), self.gerador)
            if self.gerador.random() < self.taxaMutacao:
                mutacaoInversao(filho, self.gerador)
            nova.append(self.avaliar(filho))

        self.populacao = nova
        self.ordenar()
        self.geracao += 1

    def melhor(self):
        return self.populacao[0]

    def escolherMigrantes(self, quantidade):
        validarFaixa("quantidade de migrantes", quantidade, 1, self.maximoMigrantes)
        return [list(rota) for _, rota in self.populacao[:quantidade]]

    def receberMigrantes(self, rotas):
        validas = [list(r) for r in rotas if rotaValida(r, self.n)][:self.maximoMigrantes]
        if validas:
            self.populacao = self.populacao[:-len(validas)] + [self.avaliar(r) for r in validas]
            self.ordenar()
        return len(validas)
