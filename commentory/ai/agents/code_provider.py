"""CodeProvider seam — 도구가 '코드를 어떻게 찾고 가져오나'를 추상화한다.

watch-point 에이전트의 도구들은 이 인터페이스에만 의존한다(데이터 소스를 모름).
- Phase 3a: InMemoryCodeProvider (이미 fetch된 코퍼스 위에서 동작, 네트워크 없음).
- Phase 3b: 같은 인터페이스로 FetchingCodeProvider/ClonedRepoProvider만 추가하면 됨.

비싼/전략적 연산(find_references, find_definition)은 provider 메서드로 둔다.
in-memory는 코퍼스를 순회하지만, 3b provider는 code-search 등 자체 전략으로 구현한다.
순수 파싱은 code_structure(AST)에 위임한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

try:
    from agents.code_structure import find_call_sites, get_symbol_definition
except ModuleNotFoundError:
    from commentory.ai.agents.code_structure import find_call_sites, get_symbol_definition

JAVA_SUFFIX = ".java"


@runtime_checkable
class CodeProvider(Protocol):
    def read_file(self, path: str) -> str | None:
        """파일 본문을 반환. 없거나 아직 가져오지 못했으면 None(lazy)."""
        ...

    def find_references(self, symbol: str) -> list[dict[str, Any]]:
        """symbol을 호출하는 위치들. [{"file","line","matched","snippet"}]."""
        ...

    def find_definition(self, symbol: str) -> dict[str, Any] | None:
        """symbol 정의(메서드/타입). {"file","kind","name","start_line","end_line","text"} 또는 None."""
        ...

    def known_paths(self) -> set[str]:
        """현재 접근 가능한(=이미 본) 파일 경로들. 인용 검증의 known-file 집합에 쓰인다."""
        ...


class InMemoryCodeProvider:
    """이미 fetch된 {path: content} 코퍼스 위에서 동작하는 provider(네트워크 없음)."""

    def __init__(
        self,
        repository_file_contents: dict[str, str] | list[dict[str, Any]],
        changed_contents: dict[str, str] | None = None,
        *,
        reference_context: int = 3,
    ):
        self._files = _normalize_file_contents(repository_file_contents)
        if changed_contents:
            self._files.update({
                path: content
                for path, content in changed_contents.items()
                if path and content is not None
            })
        self._reference_context = reference_context

    def read_file(self, path: str) -> str | None:
        return self._files.get(path)

    def known_paths(self) -> set[str]:
        return set(self._files)

    def find_references(self, symbol: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for path, content in self._files.items():
            sites = find_call_sites(path, content, {symbol})
            if not sites:  # None(미지원) 또는 [](매칭 없음)
                continue
            lines = content.splitlines()
            for site in sites:
                index = site["line"] - 1
                start = max(index - self._reference_context, 0)
                end = min(index + self._reference_context + 1, len(lines))
                results.append({
                    "file": path,
                    "line": site["line"],
                    "matched": site["matched_symbols"],
                    "snippet": "\n".join(lines[start:end]),
                })
        return results

    def find_definition(self, symbol: str) -> dict[str, Any] | None:
        for path, content in self._files.items():
            if not path.lower().endswith(JAVA_SUFFIX):
                continue
            definition = get_symbol_definition(content, symbol)
            if definition is not None:
                return {"file": path, **definition}
        return None


def _normalize_file_contents(file_contents: dict[str, str] | list[dict[str, Any]]) -> dict[str, str]:
    if isinstance(file_contents, dict):
        return {path: content for path, content in file_contents.items() if path and content is not None}

    normalized: dict[str, str] = {}
    for item in file_contents:
        path = item.get("path") or item.get("filename")
        content = item.get("content")
        if path and content is not None:
            normalized[path] = content
    return normalized
