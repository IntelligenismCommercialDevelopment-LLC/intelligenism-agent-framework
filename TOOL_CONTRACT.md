# Tool File Contract — IAF AI v1.0

> 外部 LLM 为 agent 编写新工具时，必须遵循本文档规定的格式。

## 文件位置

- 模板工具：`template/tools/{name}_tools.py`
- Agent 工具：`agents/{id}/tools/{name}_tools.py`

## 文件命名

- **必须**以 `_tools.py` 结尾（如 `http_tools.py`、`math_tools.py`）
- 不以 `_tools.py` 结尾的文件会被自动发现机制忽略
- 文件名中的前缀部分应描述工具类别

## 必须导出：TOOLS 字典

每个工具文件必须定义一个模块级的 `TOOLS` 字典。

### 结构

```python
TOOLS = {
    "tool_name": {
        "description": "工具功能描述，传给 LLM 作为 function calling 的 description",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "参数说明"},
                "param2": {"type": "integer", "description": "参数说明"}
            },
            "required": ["param1"]
        },
        "handler": _handler_function
    }
}
```

### 字段要求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | str | 是 | 工具描述，LLM 据此决定何时使用该工具 |
| `parameters` | dict | 是 | OpenAI function calling JSON Schema 格式 |
| `handler` | callable | 是 | 接收一个 dict 参数，返回 str |

### Handler 约定

```python
def _my_handler(args):
    """args 是一个 dict，字段与 parameters schema 对应。"""
    value = args["required_param"]
    optional = args.get("optional_param", "default")
    # ... 执行操作 ...
    return "结果字符串"
```

- 入参：单个 dict（与 parameters 中定义的字段对应）
- 返回：str（成功返回结果文本，失败返回 `"Error: ..."` 开头的字符串）
- **禁止**调用 `exit()`、`sys.exit()`、`os._exit()`
- 长操作必须设 timeout（建议 30 秒，最长 300 秒）
- 输出超过 5000 字符应截断（保护 LLM 的上下文窗口）
- handler 内部捕获所有异常，返回 Error 字符串，不要让异常冒泡
- handler 函数名建议以 `_` 开头（私有约定）

## 自动发现机制

- `tool_executor.py` 扫描 `tools/*_tools.py` 并通过 `importlib` 加载
- 如果模块有 `TOOLS` 字典，所有条目会合并到全局 `REGISTRY`
- **无需注册代码** —— 放入文件即可，下次 agent 调用时自动加载

## 最小示例

```python
"""Example tool: greeting generator."""


def _greet(args):
    name = args.get("name", "World")
    return f"Hello, {name}!"


TOOLS = {
    "greet": {
        "description": "Generate a greeting message for the given name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name to greet"
                }
            }
        },
        "handler": _greet
    }
}
```

## 验证

```bash
python3 validate.py tool
```

成功输出 `OK`，失败输出具体错误信息。
