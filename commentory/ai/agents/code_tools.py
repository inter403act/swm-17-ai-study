"""watch-point 에이전트가 호출하는 도구들.

도구는 CodeProvider에만 의존한다(데이터 소스를 모름 → 3b에서 provider만 교체).
각 도구가 반환한 코드 텍스트는 collector에 누적되어, 인용 검증의 '에이전트가 실제로 본'
코퍼스가 된다(환각 폐기의 근거).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

try:
    from agents.code_provider import CodeProvider
    from agents.code_structure import get_enclosing_context
except ModuleNotFoundError:
    from commentory.ai.agents.code_provider import CodeProvider
    from commentory.ai.agents.code_structure import get_enclosing_context

MAX_TOOL_OUTPUT_CHARS = 2000
MAX_REFERENCES = 8


def build_tools(
    provider: CodeProvider,
    collector: list[str],
    *,
    max_chars: int = MAX_TOOL_OUTPUT_CHARS,
) -> list[StructuredTool]:
    """provider/collector에 바인딩된 StructuredTool 목록을 만든다."""

    def _record(text: str) -> str:
        visible_text = text[:max_chars]
        collector.append(visible_text)
        return visible_text

    def read_file_region(path: str, start_line: int, end_line: int) -> str:
        """파일의 [start_line, end_line] 구간(1-based)을 반환한다."""
        content = provider.read_file(path)
        if content is None:
            return f"[파일을 찾을 수 없음: {path}]"
        lines = content.splitlines()
        start = max(int(start_line) - 1, 0)
        end = min(int(end_line), len(lines))
        if start >= end:
            return f"[빈 범위: {path}:{start_line}-{end_line}]"
        return _record(f"{path}:{start_line}-{end_line}\n" + "\n".join(lines[start:end]))

    def find_references(symbol: str) -> str:
        """symbol(메서드 이름)을 '호출'하는 위치들을 코드 조각과 함께 반환한다."""
        refs = provider.find_references(symbol)
        if not refs:
            return f"[호출부 없음: {symbol}]"
        blocks = [
            f"{ref['file']}:{ref['line']}\n{ref['snippet']}"
            for ref in refs[:MAX_REFERENCES]
        ]
        return _record("\n\n".join(blocks))

    def get_symbol_definition(symbol: str) -> str:
        """symbol(메서드/타입 이름)의 정의 본문과 위치를 반환한다."""
        definition = provider.find_definition(symbol)
        if definition is None:
            return f"[정의를 찾을 수 없음: {symbol}]"
        header = (
            f"{definition['file']}:{definition['start_line']}-{definition['end_line']} "
            f"({definition['kind']} {definition['name']})"
        )
        return _record(f"{header}\n{definition['text']}")

    def get_enclosing_context_tool(path: str, line: int) -> str:
        """파일의 특정 줄(1-based)을 감싸는 메서드/타입 본문을 반환한다."""
        content = provider.read_file(path)
        if content is None:
            return f"[파일을 찾을 수 없음: {path}]"
        context = get_enclosing_context(content, int(line))
        if context is None:
            return f"[둘러싼 컨텍스트 없음: {path}:{line}]"
        header = (
            f"{path}:{context['start_line']}-{context['end_line']} "
            f"({context['kind']} {context['name']})"
        )
        return _record(f"{header}\n{context['text']}")

    return [
        StructuredTool.from_function(
            read_file_region,
            name="read_file_region",
            description="파일의 특정 라인 구간(1-based)을 읽는다. 인자: path, start_line, end_line.",
        ),
        StructuredTool.from_function(
            find_references,
            name="find_references",
            description="주어진 메서드 이름을 호출하는 모든 위치(호출부)를 찾는다. 인자: symbol.",
        ),
        StructuredTool.from_function(
            get_symbol_definition,
            name="get_symbol_definition",
            description="메서드/클래스 등 심볼의 정의 본문을 가져온다. 인자: symbol.",
        ),
        StructuredTool.from_function(
            get_enclosing_context_tool,
            name="get_enclosing_context",
            description="특정 파일:라인을 감싸는 메서드/클래스 전체를 가져온다. 인자: path, line.",
        ),
    ]
