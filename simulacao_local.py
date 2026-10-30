#[imports]
import argparse

from ag import Ilha, gerarCidades, LIMITE_CIDADES, LIMITE_POPULACAO


#limites
LIMITE_ILHAS = (2, 16)
LIMITE_GERACOES = (1, 100000)


#funcoes
def inteiroEntre(minimo, maximo):
    def converter(texto):
        try:
            valor = int(texto)
        except ValueError:
            raise argparse.ArgumentTypeError(f"'{texto}' nao e um numero inteiro")
        if not minimo <= valor <= maximo:
            raise argparse.ArgumentTypeError(f"deve estar entre {minimo} e {maximo} (recebido: {valor})")
        return valor
    return converter


def lerArgumentos():
    parser = argparse.ArgumentParser(
        description="Compara ilhas isoladas com ilhas que trocam migrantes em anel",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--cidades", type=inteiroEntre(*LIMITE_CIDADES), default=80, help="quantidade de cidades do mapa")
    parser.add_argument("--ilhas", type=inteiroEntre(*LIMITE_ILHAS), default=4, help="quantidade de ilhas em cada grupo")
    parser.add_argument("--geracoes", type=inteiroEntre(*LIMITE_GERACOES), default=500, help="quantidade de geracoes")
    parser.add_argument("--populacao", type=inteiroEntre(*LIMITE_POPULACAO), default=100, help="individuos por ilha")
    parser.add_argument("--intervalo", type=inteiroEntre(*LIMITE_GERACOES), default=20, help="migra a cada N geracoes")
    parser.add_argument("--migrantes", type=inteiroEntre(1, LIMITE_POPULACAO[1] // 2), default=3, help="individuos enviados por migracao")
    parser.add_argument("--semente", type=int, default=42, help="semente do mapa de cidades")

    args = parser.parse_args()

    if args.intervalo > args.geracoes:
        parser.error(f"--intervalo ({args.intervalo}) nao pode ser maior que --geracoes ({args.geracoes})")
    if args.migrantes > args.populacao // 2:
        parser.error(f"--migrantes ({args.migrantes}) nao pode passar de metade da populacao ({args.populacao // 2})")

    return args


def melhorCusto(ilhas):
    return min(ilha.melhor()[0] for ilha in ilhas)


def migrarEmAnel(ilhas, quantidade):
    pacotes = [ilha.escolherMigrantes(quantidade) for ilha in ilhas]
    for i, ilha in enumerate(ilhas):
        ilha.receberMigrantes(pacotes[i - 1])


def main():
    args = lerArgumentos()

    cidades = gerarCidades(args.cidades, args.semente)
    isoladas = [Ilha(cidades, tamanhoPopulacao=args.populacao, semente=i) for i in range(args.ilhas)]
    conectadas = [Ilha(cidades, tamanhoPopulacao=args.populacao, semente=i) for i in range(args.ilhas)]
    passoRelatorio = max(1, args.geracoes // 10)

    print(f"{args.cidades} cidades | {args.ilhas} ilhas x {args.populacao} individuos | "
          f"migra {args.migrantes} a cada {args.intervalo} geracoes\n")
    print(f"{'geracao':>8} | {'isoladas':>10} | {'com migracao':>12}")

    try:
        for g in range(1, args.geracoes + 1):
            for ilha in isoladas + conectadas:
                ilha.proximaGeracao()

            if g % args.intervalo == 0:
                migrarEmAnel(conectadas, args.migrantes)

            if g % passoRelatorio == 0 or g == args.geracoes:
                print(f"{g:>8} | {melhorCusto(isoladas):>10.2f} | {melhorCusto(conectadas):>12.2f}")
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuario")

    custoIsoladas = melhorCusto(isoladas)
    custoConectadas = melhorCusto(conectadas)
    ganho = (custoIsoladas - custoConectadas) / custoIsoladas * 100
    print(f"\nMelhor isoladas: {custoIsoladas:.2f} | melhor com migracao: {custoConectadas:.2f} | ganho da migracao: {ganho:+.2f}%")


if __name__ == "__main__":
    main()
