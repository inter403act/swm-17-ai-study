import os
from pathlib import Path
from typing import Optional


COMMENTORY_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(COMMENTORY_ENV_FILE)


def get_solar_api_key() -> str:
    _load_env_file()
    return os.getenv("SOLAR_API_KEY") or os.getenv("UPSTAGE_API_KEY") or "{SOLAR_API_KEY}"


def get_solar_model() -> str:
    _load_env_file()
    return os.getenv("SOLAR_MODEL", "solar-pro2")


def get_solar_chat_model(*, temperature: float = 0, model: Optional[str] = None):
    """bind_tools가 가능한 ChatUpstage를 반환한다(agentic 루프용). 키 없으면 None.

    invoke_solar(단발 호출)와 달리, 호출자가 메시지 루프/도구 바인딩을 직접 제어한다.
    """
    api_key = get_solar_api_key()
    if not api_key or api_key == "{SOLAR_API_KEY}":
        return None

    from langchain_upstage import ChatUpstage

    return ChatUpstage(
        upstage_api_key=api_key,
        model_name=model or get_solar_model(),
        temperature=temperature,
    )


def invoke_solar(
    system_prompt: str,
    user_prompt: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0,
) -> Optional[str]:
    llm = get_solar_chat_model(temperature=temperature, model=model)
    if llm is None:
        return None

    from langchain_core.messages import HumanMessage, SystemMessage

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content)
