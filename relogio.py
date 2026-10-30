#[imports]
import threading


#limites
LIMITE_RELOGIO = 2 ** 62


#classes
class RelogioLamport:
    def __init__(self, idNo):
        self.idNo = idNo
        self._valor = 0
        self._trava = threading.Lock()

    @property
    def valor(self):
        with self._trava:
            return self._valor

    def tick(self):
        with self._trava:
            self._valor += 1
            return self._valor

    def enviar(self):
        return self.tick()

    def receber(self, recebido):
        if not isinstance(recebido, int) or not 0 <= recebido < LIMITE_RELOGIO:
            raise ValueError(f"relogio recebido invalido: {recebido}")

        with self._trava:
            self._valor = max(self._valor, recebido) + 1
            return self._valor

    def carimbo(self):
        with self._trava:
            return (self._valor, self.idNo)
