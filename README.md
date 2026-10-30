# Algoritmo Genético Distribuído — TSP em Ilhas

Sistema distribuído que resolve o Problema do Caixeiro Viajante (TSP) com um algoritmo genético no modelo de ilhas. Cada nó evolui a própria população e troca as melhores rotas com os vizinhos. Os nós se comunicam por gRPC, usam relógio lógico de Lamport, elegem um líder pelo algoritmo Bully e toleram a queda de nós.

---

## Pré-requisitos

- **Python 3.10 ou superior**, com o `pip` (que já vem junto na instalação padrão).
  Para verificar a versão instalada:
  ```bash
  python --version
  ```
  No Linux/macOS o comando pode ser `python3`.
- **Sistema operacional:** Windows, Linux ou macOS. O painel abre uma janela por nó apenas no Windows; nos outros sistemas, a saída de cada nó vai para um arquivo de log.
- **Para rodar em várias máquinas:** todas na mesma rede e com a porta TCP `50051` liberada no firewall (ver [Execução em várias máquinas](#execução-em-várias-máquinas)).

---

## Instalação

Os comandos abaixo são executados **dentro da pasta do projeto**.

**1. Criar o ambiente virtual**

```bash
python -m venv .venv
```

**2. Ativar o ambiente virtual**

| Terminal | Comando |
|---|---|
| Windows (Prompt de Comando) | `.venv\Scripts\activate` |
| Windows (PowerShell) | `.venv\Scripts\Activate.ps1` |
| Linux / macOS | `source .venv/bin/activate` |

Com o ambiente ativo, o terminal mostra `(.venv)` no início da linha. **A ativação vale só para o terminal atual**: cada terminal novo precisa ser ativado de novo.

> Se o PowerShell bloquear o script de ativação, use o Prompt de Comando, ou chame o Python do ambiente diretamente: `.venv\Scripts\python` no lugar de `python` em todos os comandos deste guia.

**3. Instalar as dependências**

```bash
python -m pip install -r requirements.txt
```

**4. Conferir a instalação**

```bash
python -c "import grpc, matplotlib; print('ambiente ok')"
```

Saída esperada: `ambiente ok`.

---

## Estrutura do projeto

| Arquivo | Função |
|---|---|
| `no.py` | Programa de cada nó: ilha, servidor gRPC, heartbeat, eleição e liderança. |
| `ag.py` | Algoritmo genético (população, seleção, cruzamento, mutação). |
| `eleicao.py` | Eleição de líder pelo algoritmo Bully. |
| `relogio.py` | Relógio lógico de Lamport. |
| `protos/agdistribuido.proto` | Contrato gRPC: mensagens e chamadas remotas entre os nós. |
| `agdistribuido_pb2.py`, `agdistribuido_pb2_grpc.py` | Código gerado a partir do `.proto`. Não devem ser editados. |
| `nos_local.json` | Configuração com 5 nós no mesmo computador. |
| `nos_lan.json` | Configuração com 5 nós em máquinas diferentes. |
| `painel.py`, `painel.bat` | Painel interativo para iniciar, derrubar e acompanhar os nós. |
| `simulacao_local.py` | Teste do algoritmo genético sem rede (ilhas isoladas x com migração). |
| `visualizar.py` | Gera a imagem com a melhor rota e a evolução de cada ilha. |
| `requirements.txt` | Dependências do projeto. |

---

## Execução sem o painel

Todos os comandos são executados na pasta do projeto, **com o ambiente virtual ativado**.

### 1. Testar o algoritmo genético sem rede (opcional)

```bash
python simulacao_local.py --cidades 150 --geracoes 800
```

Executa 4 ilhas isoladas e 4 ilhas com migração no mesmo processo e compara os resultados. Ao final é exibido o ganho da migração, que deve ser positivo. Todos os parâmetros são opcionais; `python simulacao_local.py --help` lista as opções e os valores padrão.

### 2. Iniciar os 5 nós no mesmo computador

Abrir **5 terminais**, ativar o ambiente virtual em cada um e executar um comando por terminal:

```bash
python no.py --id 1
python no.py --id 2
python no.py --id 3
python no.py --id 4
python no.py --id 5
```

Por padrão é usada a configuração `nos_local.json` (endereços `127.0.0.1:50051` a `127.0.0.1:50055`).

Cada nó imprime uma linha de status a cada 3 segundos:

```
[10:15:05][no 3][L=   367] geracao    273 | melhor local   3684.72 | lider: 5
```

- `L=` é o valor do relógio de Lamport do nó;
- `melhor local` é o custo da melhor rota daquela ilha (quanto menor, melhor);
- `lider` é o nó que este nó reconhece como líder.

Em poucos segundos todos os nós passam a indicar `lider: 5`. O sistema encerra sozinho quando o melhor resultado global fica 60 segundos sem melhorar (campo `estagnacaoSegundos` da configuração). Nesse momento todos os nós exibem `lider 5 mandou ENCERRAR`, e o nó 5 exibe a melhor rota encontrada.

### 3. Simular uma falha

Com os nós em execução, pressionar **Ctrl+C no terminal do nó 5** (o líder).

Em cerca de 3 segundos os demais nós detectam a falha e elegem um novo líder:

```
heartbeat ao lider 5 falhou (1/2)
heartbeat ao lider 5 falhou (2/2)
lider 5 nao responde -> iniciando ELEICAO
*** ASSUMI A LIDERANCA ***          (no terminal do nó 4)
novo lider: no 4                    (nos nós 1, 2 e 3)
```

Para religar o nó 5, executar novamente `python no.py --id 5` no mesmo terminal. Ele inicia uma eleição e retoma a liderança, e todos os nós passam a exibir `novo lider: no 5`.

### 4. Acompanhar o relógio de Lamport mensagem a mensagem (opcional)

Adicionar `--detalhado` ao iniciar um nó:

```bash
python no.py --id 1 --detalhado
```

```
[no 1][L=    46] MIGRANTES <- no 2 (3 rotas, L do remetente=45)
```

O nó 2 enviou a mensagem com L=45; ao recebê-la, o nó 1 atualizou o relógio para `max(L local, 45) + 1 = 46`.

### 5. Gerar a imagem do resultado

Ao terminar, cada nó salva seus dados em `resultados/noX.json`. Para gerar a imagem:

```bash
python visualizar.py
```

O arquivo `resultado.png` contém o mapa da melhor rota e a evolução do custo de cada ilha ao longo do tempo, com as quedas e trocas de líder marcadas. A opção `--mostrar` também abre a imagem numa janela.

---

## Execução com o painel

O painel centraliza tudo num lugar só: em vez de abrir e ativar 5 terminais e digitar um comando em cada um, a execução, a simulação de falhas, o acompanhamento e a geração da imagem são feitos por um único menu. Por baixo, ele executa os mesmos comandos da seção anterior.

### Abrir o painel

- **Windows:** dois cliques em `painel.bat`, que usa o ambiente virtual automaticamente (não precisa ativá-lo).
- **Qualquer sistema:** com o ambiente virtual ativado:
  ```bash
  python painel.py
  ```

### Opções do menu

| Opção | Ação |
|---|---|
| `1` | Mostra uma tabela com o estado de cada nó: situação, líder reconhecido, geração, melhor custo e relógio de Lamport. |
| `2` | A mesma tabela, atualizada a cada 2 segundos. `Ctrl+C` volta ao menu sem afetar os nós. |
| `3` | Inicia todos os nós da configuração atual, cada um na própria janela. |
| `4` | Inicia um nó específico (também serve para religar um nó derrubado). |
| `5` | Derruba um nó à força, simulando a queda de uma máquina. |
| `6` | Derruba todos os nós abertos pelo painel. |
| `7` | Troca a configuração: `nos_local.json`, `nos_lan.json` ou uma personalizada (quantidade de nós, cidades, população, tempo de estagnação e semente). |
| `8` | Executa a simulação local sem rede, perguntando os parâmetros. |
| `9` | Gera a imagem do resultado e a abre. |
| `0` | Sai (oferece derrubar os nós abertos antes). |

Cada pergunta do painel mostra a faixa permitida e o valor padrão, por exemplo `(2 a 10, Enter = 5)`. Valores fora da faixa são recusados.

### Roteiro de teste pelo painel

1. Opção `3`: os 5 nós abrem em janelas separadas.
2. Opção `2`: aguardar a mensagem `Consenso: os 5 nos ativos concordam que o lider e o no 5.` Pressionar `Ctrl+C` para voltar ao menu.
3. Opção `5`, nó `5`: o líder é derrubado.
4. Opção `2`: em cerca de 3 segundos o nó 5 aparece como `SEM RESPOSTA` e os demais concordam que o líder é o nó 4.
5. Opção `4`, nó `5`: o nó é religado. Na opção `2`, todos voltam a reconhecer o nó 5 como líder.
6. Aguardar o encerramento automático (todos `SEM RESPOSTA`) e usar a opção `9` para gerar a imagem.
