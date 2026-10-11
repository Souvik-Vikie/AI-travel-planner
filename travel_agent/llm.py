"""Free LLM access through HuggingFace (LangChain integration), with a safe deterministic fallback.

The agent never *depends* on the LLM producing valid output: every call goes through
`ask_json`, which validates the response and falls back to rule-based logic. That keeps the
planner reliable even on small free models (and lets eval/tests run offline).
"""
import json
import re
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage

from .config import settings


@lru_cache(maxsize=1)
def get_chat_model():
    backend = settings.llm_backend
    if backend == "auto":
        backend = "hf_endpoint" if settings.hf_token else "none"
    if backend == "none":
        return None
    from langchain_huggingface import ChatHuggingFace

    if backend == "hf_endpoint":
        from langchain_huggingface import HuggingFaceEndpoint

        llm = HuggingFaceEndpoint(repo_id=settings.hf_model_id, task="text-generation",
                                  max_new_tokens=1200, temperature=0.3,
                                  huggingfacehub_api_token=settings.hf_token)
    elif backend == "hf_local":
        from langchain_huggingface import HuggingFacePipeline

        import torch

        llm = HuggingFacePipeline.from_model_id(model_id=settings.hf_local_model_id, task="text-generation",
                                                model_kwargs={"dtype": torch.bfloat16},  # half the RAM
                                                pipeline_kwargs={"max_new_tokens": 1200, "temperature": 0.3,
                                                                 "do_sample": True, "return_full_text": False})
    else:
        raise ValueError(f"Unknown LLM_BACKEND {backend!r}")
    return ChatHuggingFace(llm=llm)


def llm_name() -> str:
    model = get_chat_model()
    if model is None:
        return "rule-based (no LLM)"
    return settings.hf_local_model_id if settings.llm_backend == "hf_local" else settings.hf_model_id


def ask_json(system: str, user: str, validate, fallback):
    """Ask the LLM for JSON. `validate(obj)` returns the cleaned value or raises.
    Returns (value, used_llm)."""
    model = get_chat_model()
    if model is None:
        return fallback(), False
    try:
        reply = model.invoke([SystemMessage(system + "\nRespond with JSON only, no prose."), HumanMessage(user)])
        return validate(_extract_json(reply.content)), True
    except Exception as exc:
        print(f"  [llm] falling back to rules ({type(exc).__name__}: {str(exc)[:80]})")
        return fallback(), False


def _extract_json(text: str):
    text = re.sub(r"```(?:json)?", "", text)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("no JSON in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj
