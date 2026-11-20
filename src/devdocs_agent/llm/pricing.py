"""models.yaml 의 price_in/price_out(USD / 1M 토큰)으로 요청 1건 비용을 계산한다."""


def cost_usd(spec: dict, prompt_tokens: int, output_tokens: int) -> float:
    return round((prompt_tokens * spec.get("price_in", 0) + output_tokens * spec.get("price_out", 0)) / 1_000_000, 6)