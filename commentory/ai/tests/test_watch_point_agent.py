import json
import textwrap
import unittest

from commentory.ai.agents import pr_analysis, watch_point_agent
from commentory.ai.agents.code_provider import InMemoryCodeProvider
from commentory.ai.agents.code_structure import get_enclosing_context, get_symbol_definition
from commentory.ai.agents.code_tools import build_tools


TASK_SERVICE = textwrap.dedent(
    """
    package sample.task;

    public class TaskService {
        public Task createTask(String title, Long ownerId) {
            Task task = new Task(title);
            tasks.add(task);
            return task;
        }

        public Task getTask(Long taskId) {
            return findTask(taskId);
        }
    }
    """
).strip() + "\n"

TASK_CONTROLLER = textwrap.dedent(
    """
    package sample.task;

    public class TaskController {
        public Task create(String title) {
            return taskService.createTask(title, 1L);
        }
    }
    """
).strip() + "\n"

TASK_SERVICE_PATH = "sample/src/main/java/sample/task/TaskService.java"
TASK_CONTROLLER_PATH = "sample/src/main/java/sample/task/TaskController.java"


class WatchPointAgentTests(unittest.TestCase):
    def test_provider_and_ast_helpers(self):
        provider = InMemoryCodeProvider(
            [{"path": TASK_CONTROLLER_PATH, "content": TASK_CONTROLLER}],
            {TASK_SERVICE_PATH: TASK_SERVICE, "empty.java": ""},
        )

        self.assertEqual(provider.read_file("empty.java"), "")
        self.assertIsNone(provider.read_file("missing.java"))

        references = provider.find_references("createTask")
        self.assertEqual(references[0]["file"], TASK_CONTROLLER_PATH)
        self.assertEqual(references[0]["line"], 5)

        definition = provider.find_definition("createTask")
        self.assertIsNotNone(definition)
        self.assertEqual(definition["kind"], "method")
        self.assertEqual(definition["start_line"], 4)

        class_definition = get_symbol_definition(TASK_SERVICE, "TaskService")
        self.assertIsNotNone(class_definition)
        self.assertEqual(class_definition["kind"], "class")

        context = get_enclosing_context(TASK_SERVICE, 6)
        self.assertIsNotNone(context)
        self.assertEqual(context["name"], "createTask")

    def test_tools_collect_only_returned_text(self):
        provider = InMemoryCodeProvider({TASK_SERVICE_PATH: TASK_SERVICE})
        collector: list[str] = []
        tools = {
            tool.name: tool
            for tool in build_tools(provider, collector, max_chars=80)
        }

        output = tools["get_symbol_definition"].invoke({"symbol": "createTask"})

        self.assertLessEqual(len(output), 80)
        self.assertEqual(collector, [output])
        self.assertIn("TaskService.java:4-8", output)

    def test_pr_analysis_gates_agentic_path_without_llm(self):
        original_key = pr_analysis.get_solar_api_key
        original_generate = pr_analysis._generate_watch_points
        try:
            pr_analysis.get_solar_api_key = lambda: "{SOLAR_API_KEY}"
            pr_analysis._generate_watch_points = lambda analyzed, evidence, changed: [{"mode": "fallback"}]

            points, mode = pr_analysis._build_watch_points(
                [{"path": TASK_SERVICE_PATH, "change_type": "logic_change"}],
                [],
                {},
                [],
            )
        finally:
            pr_analysis.get_solar_api_key = original_key
            pr_analysis._generate_watch_points = original_generate

        self.assertEqual(mode, "oneshot")
        self.assertEqual(points, [{"mode": "fallback"}])

    def test_build_impact_context_skips_docs_only_watch_points(self):
        impact = pr_analysis.build_impact_context({
            "title": "docs update",
            "body": "",
            "changed_files": [{
                "filename": "README.md",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "patch": "@@ -1 +1 @@\n-Old\n+New",
                "content": "New\n",
            }],
            "repo_tree": ["README.md"],
            "repository_file_contents": [],
        })

        self.assertEqual(impact["dependency_context"]["watch_point_mode"], "skipped")
        self.assertEqual(impact["watch_points"], [])

    def test_agent_loop_uses_tools_and_validates_seen_corpus(self):
        provider = InMemoryCodeProvider({TASK_SERVICE_PATH: TASK_SERVICE})

        class Response:
            def __init__(self, content, tool_calls=None):
                self.content = content
                self.tool_calls = tool_calls or []

        class FakeBoundModel:
            def __init__(self):
                self.calls = 0

            def invoke(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return Response(
                        "",
                        [{
                            "id": "call-1",
                            "name": "get_symbol_definition",
                            "args": {"symbol": "createTask"},
                        }],
                    )
                return Response(json.dumps({
                    "watch_points": [{
                        "observation": "createTask mutates task storage before returning.",
                        "reasoning": "The method adds the created task to tasks.",
                        "watch_for": "Confirm repeated calls do not leak state across requests.",
                        "citations": [{
                            "file": "TaskService.java",
                            "lines": "6",
                            "quote": "tasks.add(task);",
                        }],
                        "anchored_on": [],
                    }],
                }))

        class FakeModel:
            def bind_tools(self, tools):
                return FakeBoundModel()

        original_model = watch_point_agent.get_solar_chat_model
        try:
            watch_point_agent.get_solar_chat_model = lambda: FakeModel()
            result = watch_point_agent.run_watch_point_agent(
                [{
                    "path": TASK_SERVICE_PATH,
                    "change_type": "logic_change",
                    "diff_snippet": "+        tasks.add(task);",
                    "dead_parameters": [],
                    "shared_mutable_fields": [],
                    "removed_access_control": [],
                }],
                provider,
            )
        finally:
            watch_point_agent.get_solar_chat_model = original_model

        self.assertEqual(result[0]["citations"][0]["quote"], "tasks.add(task);")

    def test_one_shot_watch_points_swallow_llm_exception(self):
        original_invoke = pr_analysis.invoke_solar
        try:
            pr_analysis.invoke_solar = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network down"))
            result = pr_analysis._generate_watch_points(
                [{
                    "path": TASK_SERVICE_PATH,
                    "change_type": "logic_change",
                    "diff_snippet": "+        tasks.add(task);",
                    "dead_parameters": [],
                    "shared_mutable_fields": [],
                    "removed_access_control": [],
                }],
                [],
                {TASK_SERVICE_PATH: TASK_SERVICE},
            )
        finally:
            pr_analysis.invoke_solar = original_invoke

        self.assertEqual(result, [])

    def test_agent_loop_respects_tool_call_cap(self):
        class CountingProvider:
            def __init__(self):
                self.definition_calls = 0

            def read_file(self, path):
                return "public class TaskService {}\n"

            def find_references(self, symbol):
                return []

            def find_definition(self, symbol):
                self.definition_calls += 1
                return {
                    "file": TASK_SERVICE_PATH,
                    "kind": "class",
                    "name": "TaskService",
                    "start_line": 1,
                    "end_line": 1,
                    "text": "public class TaskService {}",
                }

            def known_paths(self):
                return {TASK_SERVICE_PATH}

        class Response:
            def __init__(self, content, tool_calls=None):
                self.content = content
                self.tool_calls = tool_calls or []

        class FakeBoundModel:
            def __init__(self):
                self.calls = 0

            def invoke(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return Response(
                        "",
                        [
                            {"id": "call-1", "name": "get_symbol_definition", "args": {"symbol": "TaskService"}},
                            {"id": "call-2", "name": "get_symbol_definition", "args": {"symbol": "TaskService"}},
                            {"id": "call-3", "name": "get_symbol_definition", "args": {"symbol": "TaskService"}},
                        ],
                    )
                return Response(json.dumps({
                    "watch_points": [{
                        "observation": "TaskService definition was inspected.",
                        "reasoning": "The tool returned the class definition.",
                        "watch_for": "Confirm the inspected definition is relevant.",
                        "citations": [{
                            "file": "TaskService.java",
                            "lines": "1",
                            "quote": "public class TaskService {}",
                        }],
                        "anchored_on": [],
                    }],
                }))

        class FakeModel:
            def bind_tools(self, tools):
                return FakeBoundModel()

        provider = CountingProvider()
        original_model = watch_point_agent.get_solar_chat_model
        try:
            watch_point_agent.get_solar_chat_model = lambda: FakeModel()
            result = watch_point_agent.run_watch_point_agent(
                [{
                    "path": TASK_SERVICE_PATH,
                    "change_type": "logic_change",
                    "diff_snippet": "public class TaskService {}",
                    "dead_parameters": [],
                    "shared_mutable_fields": [],
                    "removed_access_control": [],
                }],
                provider,
                max_tool_calls=1,
            )
        finally:
            watch_point_agent.get_solar_chat_model = original_model

        self.assertEqual(provider.definition_calls, 1)
        self.assertEqual(result[0]["citations"][0]["quote"], "public class TaskService {}")


if __name__ == "__main__":
    unittest.main()
