import json
import time
import re
from pydantic import ValidationError

from ..application.ports import ArtifactStore, AgentFailure


class OpenAIStructuredAgent:
    """Bounded Responses API calls. No fallback disguises a failed LLM call."""

    def __init__(self, api_key, model, store: ArtifactStore, max_calls=24, max_output_tokens=8000, client=None,
                 reasoning_effort=None):
        if not api_key or not model:
            raise ValueError("OPENAI_API_KEY and an explicit model are required")
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, timeout=60, max_retries=0)
        self.client, self.model, self.store = client, model, store
        self.max_calls, self.max_output_tokens = max_calls, max_output_tokens
        self.calls = 0
        self.reasoning_effort = reasoning_effort

    def propose(self, stage, instructions, context, output):
        if self.calls >= self.max_calls:
            raise AgentFailure("LLM call budget exhausted",stage=stage,error_kind='budget')
        self.calls += 1
        started = time.monotonic()
        self.store.event(stage, "llm_started", {"call": self.calls, "model": self.model})
        try:
            response = self.client.responses.parse(
                model=self.model, instructions=instructions,
                input=json.dumps(context, ensure_ascii=False), text_format=output,
                max_output_tokens=self.max_output_tokens, store=False,
                **({"reasoning": {"effort": self.reasoning_effort}} if self.reasoning_effort else {}),
            )
            if response.output_parsed is None or getattr(response, "status", "completed") != "completed":
                raise AgentFailure("LLM refused or returned incomplete structured output",stage=stage,error_kind='incomplete')
            parsed = output.model_validate(response.output_parsed)
        except Exception as error:
            errors=[]
            kind=getattr(error,'error_kind','provider')
            if isinstance(error,ValidationError):
                kind='validation'
                fields=set()
                def collect(schema):
                    if isinstance(schema,dict):
                        fields.update(schema.get('properties',{}))
                        for child in schema.values():collect(child)
                    elif isinstance(schema,list):
                        for child in schema:collect(child)
                collect(output.model_json_schema())
                errors=[{'type':issue['type'],'location':[str(part) if isinstance(part,int) or part in fields else '<field>' for part in issue['loc']]} for issue in error.errors(include_input=False,include_context=False,include_url=False)[:12]]
            elif type(error).__name__=='LengthFinishReasonError':kind='incomplete'
            elif type(error).__name__ in ('APITimeoutError','TimeoutError'):kind='timeout'
            elif type(error).__name__=='AuthenticationError':kind='authentication'
            elif type(error).__name__=='RateLimitError':kind='rate_limit'
            self.store.event(stage, "llm_failed", {"error_type": type(error).__name__, "call": self.calls,'error_kind':kind,'schema_errors':errors})
            # Provider exceptions can contain request content; do not copy them to logs.
            raise AgentFailure(f"LLM stage {stage} failed ({type(error).__name__}); inspect provider/configuration",stage=stage,error_kind=kind,validation_errors=errors) from None
        usage = getattr(response, "usage", None)
        safe_stage = re.sub(r"[^A-Za-z0-9_.-]", "_", stage)
        self.store.write(f"llm_outputs/{self.calls:02d}-{safe_stage}.json", parsed.model_dump(mode="json"))
        self.store.event(stage, "llm_completed", {
            "call": self.calls, "response_id": getattr(response, "id", None),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        })
        return parsed
