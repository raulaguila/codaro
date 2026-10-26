"""Generation throughput: observed text estimates and explicitly sourced API metrics."""

import math
import time


class GenerationRate:
    def __init__(self, *, clock=time.monotonic, encoder=None):
        self.clock, self.encoder = clock, encoder
        self.reset()

    def reset(self):
        self.first_at = self.last_at = None
        self.fragments = self.bytes = self.first_bytes = 0
        self.text = self.first_text = ""
        self.reported = None
        self.source = ""

    def append(self, text):
        if not text:
            return
        self.fragments += 1
        self.bytes += len(text.encode("utf-8", errors="replace"))
        self.text = (self.text + text)[:16000]
        self.last_at = self.clock()
        if self.first_at is None:
            self.first_at = self.last_at
            self.first_bytes, self.first_text = self.bytes, self.text

    def report(self, tokens, *, generation_seconds=None, request_seconds=None):
        if type(tokens) is not int or not 0 < tokens <= 2_000_000:
            return
        duration = generation_seconds if generation_seconds is not None else request_seconds
        if type(duration) not in (float, int) or not math.isfinite(duration) or duration <= 0:
            return
        self.reported = tokens / duration
        self.source = "provider" if generation_seconds is not None else "request"

    def value(self):
        if self.reported is not None:
            return self.reported, self.source
        if self.fragments < 2 or self.first_at is None or self.last_at - self.first_at < 0.25:
            return None
        count = (self.bytes - self.first_bytes) / 4
        if self.encoder is not None and self.bytes <= 16000:
            count = len(self.encoder.encode(self.text, disallowed_special=())) - len(
                self.encoder.encode(self.first_text, disallowed_special=())
            )
        return max(0, count) / (self.last_at - self.first_at), "estimate"

    def display(self):
        metric = self.value()
        if metric is None:
            return "", "Velocidade disponível após receber mais fragmentos ou métricas da API."
        value, source = metric
        number = f"{value:.1f}" if value < 10 else f"{value:.0f}"
        descriptions = {
            "estimate": (
                "Estimativa do texto recebido entre fragmentos. Usa tokenizer local quando "
                "configurado; senão UTF-8/4. Exclui a espera pelo primeiro fragmento."
            ),
            "provider": (
                "Tokens de saída / tempo de geração informados pelo provedor. "
                "Podem incluir raciocínio e argumentos de ferramentas."
            ),
            "request": (
                "Média da chamada: tokens de saída informados pela API / tempo da requisição, "
                "incluindo espera e processamento de entrada."
            ),
        }
        label = ("≈" if source == "estimate" else "") + number + " t/s"
        if source == "request":
            label += " méd."
        return label, descriptions[source]
