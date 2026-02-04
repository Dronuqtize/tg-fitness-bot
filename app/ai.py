from __future__ import annotations

from typing import Iterable

from openai import OpenAI


SYSTEM_PROMPT = (
    "Ты фитнес-ассистент. Давай короткие, практичные советы по тренировкам, "
    "питанию и восстановлению для похудения. Не давай медицинских рекомендаций, "
    "не обсуждай дозировки препаратов. Если данных мало — предложи, что добавить."
)


def generate_advice(api_key: str, context_lines: Iterable[str]) -> str:
    client = OpenAI(api_key=api_key)
    user_content = "\n".join(context_lines).strip()
    if not user_content:
        user_content = "Данных пока мало."

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.4,
        max_output_tokens=220,
    )
    return response.output_text.strip()
