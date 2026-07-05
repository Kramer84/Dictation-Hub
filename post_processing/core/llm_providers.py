import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, List, Optional, Type, TypeVar, Union

import requests
from pydantic import BaseModel, Field, ValidationError

# Initialize as part of the module hierarchy to defer to orchestrator config
logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class ChatMessage(BaseModel):
    role: str = Field(
        ...,
        description="The role of the message author: 'system', 'user', 'assistant', or 'tool'.",
    )
    content: str = Field(..., description="The string payload of the message context.")
    name: Optional[str] = Field(
        None,
        description="Optional identifier for the participant, useful for multi-turn routing.",
    )


class LLMConfig(BaseModel):
    temperature: float = Field(0.1, ge=0.0, le=2.0, description="Sampling temperature.")
    max_tokens: Optional[int] = Field(
        None, description="Upper bound bound on tokens to generate."
    )
    top_p: float = Field(
        1.0, ge=0.0, le=1.0, description="Nucleus sampling probability mass threshold."
    )
    seed: Optional[int] = Field(
        None,
        description="Deterministic sampling seed if supported by provider backend.",
    )
    timeout: float = Field(30.0, description="HTTP request timeout ceiling in seconds.")
    max_retries: int = Field(
        3, ge=0, description="Transient error connection retry threshold."
    )
    backoff_factor: float = Field(
        2.0, description="Multiplier for exponential backoff sequence calculations."
    )
    keep_alive: Optional[Union[int, str]] = Field(
        None,
        description="Duration to keep the model loaded in GPU memory (e.g., 0 to unload immediately, '5m' for 5 minutes). Primarily for local runtimes.",
    )


class UsageMetrics(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class GenerationResult(BaseModel):
    raw_content: str
    parsed_object: Optional[Any] = None
    metrics: UsageMetrics


class LLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        messages: List[ChatMessage],
        config: Optional[LLMConfig] = None,
        response_schema: Optional[Type[T]] = None,
    ) -> GenerationResult:
        pass

    @abstractmethod
    def generate_stream(
        self, messages: List[ChatMessage], config: Optional[LLMConfig] = None
    ) -> Generator[str, None, None]:
        pass

    def _execute_with_retry(
        self, request_fn: callable, config: LLMConfig
    ) -> requests.Response:
        retries = 0
        backoff = 1.0
        logger.debug(f"Initializing HTTP execution with max_retries={config.max_retries}, backoff_factor={config.backoff_factor}")
        
        while True:
            try:
                logger.debug(f"HTTP Request Attempt {retries + 1}/{config.max_retries + 1}")
                response = request_fn()
                if response.status_code in [429, 500, 502, 503, 504]:
                    if retries >= config.max_retries:
                        logger.error(f"HTTP {response.status_code} received. Max retries exhausted.")
                        response.raise_for_status()
                    logger.warning(
                        f"Transient HTTP Status {response.status_code} observed. Retrying in {backoff}s..."
                    )
                else:
                    response.raise_for_status()
                    logger.debug(f"HTTP request successful (Status {response.status_code}).")
                    return response
            except requests.exceptions.RequestException as e:
                if retries >= config.max_retries:
                    logger.error(
                        f"Execution boundary broken. Total retries ({config.max_retries}) exhausted. Final Error: {str(e)}"
                    )
                    raise e
                logger.warning(f"Connection dropped: {str(e)}. Attempting retry state in {backoff}s.")
            
            time.sleep(backoff)
            backoff *= config.backoff_factor
            retries += 1

    def _parse_and_validate_json(self, raw_text: str, schema: Type[T]) -> Optional[T]:
        logger.debug(f"Attempting JSON extraction and validation against schema: {schema.__name__}")
        try:
            clean_text = raw_text.strip()
            if clean_text.startswith("```json"):
                logger.debug("Detected markdown ```json code block. Stripping formatting.")
                clean_text = (
                    clean_text.split("```json", 1)[1].rsplit("```", 1)[0].strip()
                )
            elif clean_text.startswith("```"):
                logger.debug("Detected generic markdown ``` code block. Stripping formatting.")
                clean_text = clean_text.split("```", 1)[1].rsplit("```", 1)[0].strip()
            
            parsed_obj = schema.model_validate_json(clean_text)
            logger.debug(f"JSON successfully validated against {schema.__name__}.")
            return parsed_obj
            
        except (ValidationError, ValueError) as json_err:
            logger.error(
                f"Structural validation against Schema broken: {str(json_err)}"
            )
            logger.debug(f"Offending text payload received:\n{raw_text}")
            return None


class MistralProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "mistral-large-latest"):
        self.api_key = api_key
        self.url = "https://api.mistral.ai/v1/chat/completions"
        self.model = model
        logger.info(f"Initialized MistralProvider with model '{self.model}' targeting {self.url}")

    def _build_payload(
        self,
        messages: List[ChatMessage],
        config: LLMConfig,
        response_schema: Optional[Type[BaseModel]] = None,
    ) -> Dict[str, Any]:
        logger.debug("Constructing request payload for Mistral API.")
        payload = {
            "model": self.model,
            "messages": [msg.model_dump(exclude_none=True) for msg in messages],
            "temperature": config.temperature,
            "top_p": config.top_p,
        }
        if config.max_tokens:
            payload["max_tokens"] = config.max_tokens
        if config.seed is not None:
            payload["random_seed"] = config.seed
        if response_schema:
            payload["response_format"] = {"type": "json_object"}
            logger.debug("Enforcing 'json_object' response_format for schema extraction.")
            
        logger.debug(f"Payload configuration: temperature={config.temperature}, max_tokens={config.max_tokens}, seed={config.seed}")
        return payload

    def generate(
        self,
        messages: List[ChatMessage],
        config: Optional[LLMConfig] = None,
        response_schema: Optional[Type[T]] = None,
    ) -> GenerationResult:
        logger.info(f"Starting generation request with MistralProvider (Messages: {len(messages)})")
        cfg = config or LLMConfig()
        payload = self._build_payload(messages, cfg, response_schema)
        payload["stream"] = False
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        start_time = time.perf_counter()
        response = self._execute_with_retry(
            lambda: requests.post(
                self.url, headers=headers, json=payload, timeout=cfg.timeout
            ),
            cfg,
        )
        latency = (time.perf_counter() - start_time) * 1000.0
        
        response_json = response.json()
        raw_content = response_json["choices"][0]["message"]["content"]
        usage_data = response_json.get("usage", {})
        
        metrics = UsageMetrics(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            latency_ms=latency,
        )
        logger.info(f"Generation completed in {latency:.2f}ms. Tokens generated: {metrics.completion_tokens}")

        parsed_obj = None
        if response_schema and raw_content:
            parsed_obj = self._parse_and_validate_json(raw_content, response_schema)
            
        return GenerationResult(
            raw_content=raw_content, parsed_object=parsed_obj, metrics=metrics
        )

    def generate_stream(
        self, messages: List[ChatMessage], config: Optional[LLMConfig] = None
    ) -> Generator[str, None, None]:
        logger.info(f"Starting streaming generation request with MistralProvider (Messages: {len(messages)})")
        cfg = config or LLMConfig()
        payload = self._build_payload(messages, cfg)
        payload["stream"] = True
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        logger.debug("Opening HTTP stream connection...")
        response = requests.post(
            self.url, headers=headers, json=payload, timeout=cfg.timeout, stream=True
        )
        response.raise_for_status()
        
        chunk_count = 0
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode("utf-8").strip()
                if decoded_line.startswith("data:"):
                    data_str = decoded_line[5:].strip()
                    if data_str == "[DONE]":
                        logger.debug("Received stream termination marker [DONE].")
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        delta = chunk_json["choices"][0]["delta"].get("content", "")
                        if delta:
                            chunk_count += 1
                            yield delta
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to decode stream JSON chunk: {data_str}")
                        continue
        logger.info(f"Streaming completed. Yielded {chunk_count} valid text chunks.")


class LocalProvider(LLMProvider):
    def __init__(self, endpoint: str, model: str):
        self.endpoint = endpoint
        self.model = model
        logger.info(f"Initialized LocalProvider with model '{self.model}' targeting {self.endpoint}")

    def _build_payload(
        self,
        messages: List[ChatMessage],
        config: LLMConfig,
        response_schema: Optional[Type[BaseModel]] = None,
    ) -> Dict[str, Any]:
        logger.debug("Constructing request payload for Local Engine API.")
        payload = {
            "model": self.model,
            "messages": [msg.model_dump(exclude_none=True) for msg in messages],
            "temperature": config.temperature,
            "top_p": config.top_p,
        }
        if config.max_tokens:
            payload["max_tokens"] = config.max_tokens
        if config.seed is not None:
            payload["seed"] = config.seed
        if config.keep_alive is not None:
            payload["keep_alive"] = config.keep_alive
            logger.debug(f"Applying keep_alive policy: {config.keep_alive}")
            
        if response_schema:
            payload["response_format"] = {
                "type": "json_object",
                "schema": response_schema.model_json_schema(),
            }
            logger.debug("Injected JSON schema definition into payload.")
            
        return payload

    def generate(
        self,
        messages: List[ChatMessage],
        config: Optional[LLMConfig] = None,
        response_schema: Optional[Type[T]] = None,
    ) -> GenerationResult:
        logger.info(f"Starting generation request with LocalProvider (Messages: {len(messages)})")
        cfg = config or LLMConfig()
        payload = self._build_payload(messages, cfg, response_schema)
        payload["stream"] = False
        headers = {"Content-Type": "application/json"}
        
        start_time = time.perf_counter()
        response = self._execute_with_retry(
            lambda: requests.post(
                self.endpoint, headers=headers, json=payload, timeout=cfg.timeout
            ),
            cfg,
        )
        latency = (time.perf_counter() - start_time) * 1000.0
        
        response_json = response.json()
        raw_content = response_json["choices"][0]["message"]["content"]
        usage_data = response_json.get("usage", {})
        
        metrics = UsageMetrics(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            latency_ms=latency,
        )
        logger.info(f"Generation completed in {latency:.2f}ms. Tokens generated: {metrics.completion_tokens}")

        parsed_obj = None
        if response_schema and raw_content:
            parsed_obj = self._parse_and_validate_json(raw_content, response_schema)
            
        return GenerationResult(
            raw_content=raw_content, parsed_object=parsed_obj, metrics=metrics
        )

    def generate_stream(
        self, messages: List[ChatMessage], config: Optional[LLMConfig] = None
    ) -> Generator[str, None, None]:
        logger.info(f"Starting streaming generation request with LocalProvider (Messages: {len(messages)})")
        cfg = config or LLMConfig()
        payload = self._build_payload(messages, cfg)
        payload["stream"] = True
        headers = {"Content-Type": "application/json"}
        
        logger.debug("Opening HTTP stream connection...")
        response = requests.post(
            self.endpoint,
            headers=headers,
            json=payload,
            timeout=cfg.timeout,
            stream=True,
        )
        response.raise_for_status()
        
        chunk_count = 0
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode("utf-8").strip()
                if decoded_line.startswith("data:"):
                    data_str = decoded_line[5:].strip()
                    if data_str == "[DONE]":
                        logger.debug("Received stream termination marker [DONE].")
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        delta = chunk_json["choices"][0]["delta"].get("content", "")
                        if delta:
                            chunk_count += 1
                            yield delta
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to decode stream JSON chunk: {data_str}")
                        continue
        logger.info(f"Streaming completed. Yielded {chunk_count} valid text chunks.")


if __name__ == "__main__":
    # Local fallback logger initialization for standalone blueprint execution
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logger.info("--- Designing Validation Blueprint Structure ---")

    class EntityExtraction(BaseModel):
        company_name: str = Field(..., description="The name of the entity.")
        valuation_billion: float = Field(
            ..., description="Market valuation listed in billions USD."
        )
        core_technology: str = Field(
            ..., description="The key domain focus of the organization."
        )
        tags: List[str] = Field(
            default_factory=list, description="Keywords summarizing the entity."
        )

    pipeline_context = [
        ChatMessage(
            role="system",
            content="You are a strict financial extraction engine. Output your answers in complete compliance with schemas asked.",
        ),
        ChatMessage(
            role="user",
            content="Extract details from this notice: 'Yesterday, AcmeAI closed its funding round valuing the startup at $4.2 billion. Their transformer core framework remains unmatched.'",
        ),
    ]
    custom_config = LLMConfig(temperature=0.0, timeout=15.0)
    
    logger.info("[Blueprint Ready] Wire your runtime credentials to activate processing:\n")
    print(
        '''
        # Verification Pipeline Scaffold:
        # -----------------------------
        # provider = MistralProvider(api_key=os.environ.get("MISTRAL_API_KEY"))
        # response = provider.generate(messages=pipeline_context, config=custom_config, response_schema=EntityExtraction)
        # if response.parsed_object:
        #     logger.info(f"Validated entity: {response.parsed_object.company_name} | Valuation: {response.parsed_object.valuation_billion}B")
        '''
    )
