"""
LLM 客户端抽象层。

支持:
- kimi: 月之暗面 Moonshot，OpenAI-compatible API
- openai: OpenAI API
- anthropic: Anthropic Claude API
- ollama: 本地 Ollama HTTP API

API Key 读取优先级:
1. 传入的 api_key 参数
2. 配置文件指定的 api_key_file
3. 环境变量 (MOONSHOT_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY)
"""
import os
import json
import re
from typing import Optional, Dict, Any


class LLMClient:
    """统一的 LLM 调用客户端"""

    ENV_KEY_MAP = {
        "kimi": "MOONSHOT_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "ollama": None,
    }

    DEFAULT_API_BASE = {
        "kimi": "https://api.moonshot.cn/v1",
        "openai": "https://api.openai.com/v1",
        "ollama": "http://localhost:11434",
    }

    def __init__(self, provider: str, model: str, api_key: str = None,
                 api_base: str = None, timeout: int = 120, **kwargs):
        self.provider = provider.lower().strip()
        self.model = model
        self.api_base = api_base or self.DEFAULT_API_BASE.get(self.provider)
        self.timeout = timeout
        self.extra = kwargs
        self._api_key = api_key
        self._client = None

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> Optional["LLMClient"]:
        """从 settings.yaml 配置构造 LLMClient"""
        cfg = config.get("ai_briefing", {})
        if not cfg.get("enabled", False):
            return None

        provider = cfg.get("llm_provider", "kimi")
        model = cfg.get("model", "moonshot-v1-32k")
        api_base = cfg.get("api_base")
        api_key_file = cfg.get("api_key_file", "config/.llm_api_key")

        # 允许通过环境变量覆盖 api_base，便于 CI/多环境切换
        env_api_base = os.environ.get(f"{provider.upper()}_API_BASE") or os.environ.get("LLM_API_BASE")
        if env_api_base:
            api_base = env_api_base

        api_key = cls._load_key_from_file(api_key_file)
        return cls(provider=provider, model=model, api_key=api_key,
                   api_base=api_base)

    @staticmethod
    def _load_key_from_file(path: str) -> Optional[str]:
        """从文件加载 API key，支持相对项目根目录路径。

        文件内容允许包含以 # 开头的注释行，取第一个非注释行作为 key。
        """
        if not path:
            return None
        candidates = [path]
        if not os.path.isabs(path):
            candidates.extend([
                os.path.join(os.path.dirname(__file__), "..", path),
                os.path.join(os.getcwd(), path),
            ])

        for p in candidates:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        return line
            except Exception:
                return None
        return None

    def _get_api_key(self) -> Optional[str]:
        """获取 API key"""
        if self._api_key:
            return self._api_key

        env_var = self.ENV_KEY_MAP.get(self.provider)
        if env_var:
            key = os.environ.get(env_var, "").strip()
            if key:
                return key

        # 兼容通用环境变量
        for ev in ["LLM_API_KEY", "OPENAI_API_KEY"]:
            key = os.environ.get(ev, "").strip()
            if key:
                return key
        return None

    def _get_openai_client(self):
        """延迟初始化 OpenAI 兼容客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise ImportError("使用 kimi/openai 提供商需要安装 openai SDK: pip install openai") from e
            kwargs = {"api_key": self._get_api_key(), "timeout": self.timeout}
            if self.api_base:
                kwargs["base_url"] = self.api_base
            self._client = OpenAI(**kwargs)
        return self._client

    def _get_anthropic_client(self):
        """延迟初始化 Anthropic 客户端"""
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise ImportError("使用 anthropic 提供商需要安装 anthropic SDK: pip install anthropic") from e
            self._client = Anthropic(api_key=self._get_api_key(), timeout=self.timeout)
        return self._client

    def complete(self, prompt: str, system: str = None,
                 max_tokens: int = 4096, temperature: float = 0.3,
                 output_schema: Dict[str, Any] = None) -> Optional[str]:
        """
        调用 LLM 完成一次补全。

        Args:
            prompt: 用户提示词
            system: 系统提示词
            max_tokens: 最大输出 token
            temperature: 采样温度
            output_schema: 若提供，会在 prompt 中追加 JSON Schema 约束，
                           并尝试把返回结果解析为 JSON（失败返回 None）

        Returns:
            原始文本字符串；若 output_schema 提供且解析成功，返回 JSON 字符串。
            调用失败返回 None。
        """
        try:
            if output_schema:
                prompt = self._inject_json_schema(prompt, output_schema)

            if self.provider in ("kimi", "openai"):
                raw = self._complete_openai(prompt, system, max_tokens, temperature, output_schema)
            elif self.provider == "anthropic":
                raw = self._complete_anthropic(prompt, system, max_tokens, temperature)
            elif self.provider == "ollama":
                raw = self._complete_ollama(prompt, system, max_tokens, temperature)
            else:
                raise ValueError(f"不支持的 LLM 提供商: {self.provider}")

            if raw is None:
                return None

            if output_schema:
                parsed = self._extract_json(raw)
                if parsed is None:
                    print(f"     [WARN] LLM 返回非合法 JSON（model={self.model}, base={self.api_base}）")
                    print(f"              原始内容长度={len(raw)}, 前200字={raw[:200]!r}")
                    return None
                return json.dumps(parsed, ensure_ascii=False)
            return raw
        except Exception as e:
            print(f"     [WARN] LLM 调用失败 [{self.provider}/{self.model}]: {e}")
            return None

    def _inject_json_schema(self, prompt: str, schema: Dict[str, Any]) -> str:
        """在 prompt 末尾追加 JSON 输出要求"""
        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        suffix = (
            "\n\n【重要】你必须只输出合法的 JSON 对象，不要包含 Markdown 代码块、"
            "不要包含解释说明。JSON Schema 如下:\n" + schema_text
        )
        return prompt + suffix

    def _complete_openai(self, prompt: str, system: str,
                         max_tokens: int, temperature: float,
                         output_schema: Dict[str, Any] = None) -> Optional[str]:
        client = self._get_openai_client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # 部分 OpenAI-compatible 接口支持 json_object，可提升 JSON 输出稳定性
        # 但 kimi coding 端点对此支持不稳定，先默认不启用，后续可按需开启
        # if output_schema:
        #     kwargs.setdefault("response_format", {"type": "json_object"})
        response = client.chat.completions.create(**kwargs)
        if response.choices and response.choices[0].message:
            return response.choices[0].message.content
        return None

    def _complete_anthropic(self, prompt: str, system: str,
                            max_tokens: int, temperature: float) -> Optional[str]:
        client = self._get_anthropic_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        if response.content:
            return response.content[0].text
        return None

    def _complete_ollama(self, prompt: str, system: str,
                         max_tokens: int, temperature: float) -> Optional[str]:
        import requests
        url = (self.api_base or self.DEFAULT_API_BASE["ollama"]).rstrip("/") + "/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system or "",
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return data.get("response")

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """从文本中提取 JSON 对象"""
        if not text:
            return None
        text = text.strip()
        # 去掉 Markdown 代码块
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试从文本中找第一个 { ... }
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        return None
