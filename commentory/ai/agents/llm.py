import os
from typing import Optional


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv()


def get_solar_api_key() -> str:
    _load_env_file()
    return os.getenv("SOLAR_API_KEY") or os.getenv("UPSTAGE_API_KEY") or "{SOLAR_API_KEY}"


def get_solar_model() -> str:
    _load_env_file()
    return os.getenv("SOLAR_MODEL", "solar-pro2")


def invoke_solar(
    system_prompt: str,
    user_prompt: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0,
) -> Optional[str]:
    api_key = get_solar_api_key()
    if not api_key or api_key == "{SOLAR_API_KEY}":
        return None

    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_upstage import ChatUpstage

    llm = ChatUpstage(
        upstage_api_key=api_key,
        model_name=model or get_solar_model(),
        temperature=temperature,
    )
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content)
