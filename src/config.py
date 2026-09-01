"""配置加载：parameters.toml（标准库 tomllib，零第三方依赖）

用法:
    import sys; sys.path.insert(0, "src")
    from config import load_config, rules, strategy, section

规则版本号与文件内容绑定；变更配置后请 bump rules_version。
"""
from __future__ import annotations

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "parameters.toml"
RULES_VERSION = "v4.0"


def load_config(path: pathlib.Path | str | None = None) -> dict:
    p = pathlib.Path(path) if path else DEFAULT_CONFIG
    with open(p, "rb") as f:
        return tomllib.load(f)


CONFIG = load_config()


def rules() -> list[dict]:
    """知识表：37 条规则（带置信度/出处）"""
    return list(CONFIG.get("rule", []))


def strategy(name: str) -> dict:
    """引擎参数：strategy.<name> 小节"""
    return dict(CONFIG.get("strategy", {}).get(name, {}))


def section(name: str) -> dict:
    return dict(CONFIG.get(name, {}))


def rules_version() -> str:
    return RULES_VERSION


if __name__ == "__main__":
    import json

    print("rules_version:", rules_version())
    print("rules count:", len(rules()))
    print("strategies:", list(CONFIG.get("strategy", {}).keys()))
    print(json.dumps(strategy("huigui"), ensure_ascii=False, indent=2))
