"""
tools.py — The research agent's tools over the local knowledge base.

  search_docs  keyword search, returns the best matching sections
  read_doc     full text of one document
  calculator   arithmetic, evaluated safely (AST whitelist, no eval())
"""

import ast
import operator
import re
from pathlib import Path

CORPUS_DIR = Path(__file__).with_name("corpus")

TOOL_SPECS = [
    {
        "name": "search_docs",
        "description": "Search the Acme Cloud handbook (plans, pricing, SLA, on-call, expenses). "
                       "Returns the best matching sections with the document they come from.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Keywords to look for"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_doc",
        "description": "Read a whole handbook document by name (plans, sla, oncall, expenses).",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Document name, without extension"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression with + - * / ** ( ) and decimals, e.g. '29 * 12 * 0.85'.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    """Raised for invalid tool input; reported back to the model as an error."""


def _sections() -> list[tuple[str, str]]:
    sections = []
    for path in sorted(CORPUS_DIR.glob("*.md")):
        for block in re.split(r"\n(?=## )", path.read_text(encoding="utf-8")):
            sections.append((path.stem, block.strip()))
    return sections


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def search_docs(query: str, limit: int = 3) -> str:
    terms = _words(query)
    if not terms:
        raise ToolError("query has no searchable words")
    scored = []
    for doc, text in _sections():
        words = _words(text)
        score = len(terms & words)
        if score:
            scored.append((score, doc, text))
    scored.sort(key=lambda s: s[0], reverse=True)
    if not scored:
        return "No matching sections."
    return "\n\n---\n\n".join(f"[{doc}]\n{text}" for _, doc, text in scored[:limit])


def read_doc(name: str) -> str:
    path = CORPUS_DIR / f"{Path(name).stem}.md"
    if not path.exists():
        available = ", ".join(p.stem for p in sorted(CORPUS_DIR.glob("*.md")))
        raise ToolError(f"unknown document '{name}'. Available: {available}")
    return path.read_text(encoding="utf-8")


_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval(node.right)) > 100:
            raise ToolError("exponent too large")
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ToolError("only numbers and + - * / ** ( ) are allowed")


def calculator(expression: str) -> str:
    try:
        tree = ast.parse(expression.replace(",", ""), mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"invalid expression: {exc.msg}") from exc
    try:
        result = _eval(tree.body)
    except ZeroDivisionError as exc:
        raise ToolError("division by zero") from exc
    return str(round(result, 6))


REGISTRY = {"search_docs": search_docs, "read_doc": read_doc, "calculator": calculator}


def run_tool(name: str, args: dict) -> str:
    if name not in REGISTRY:
        raise ToolError(f"unknown tool '{name}'")
    try:
        return REGISTRY[name](**args)
    except TypeError as exc:
        raise ToolError(f"bad arguments: {exc}") from exc
