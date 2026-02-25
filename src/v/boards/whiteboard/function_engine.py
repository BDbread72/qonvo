"""
Blueprint-style function execution engine
- validate_function_graph: graph validation
- BlueprintExecutionWorker: QThread-based async execution with dual-flow
  (exec chain + data pin backward resolution)
"""
import json
import os
import re

from PyQt6.QtCore import QThread, pyqtSignal

from v.provider import GeminiProvider, ChatMessage, get_default_options
from .function_types import (
    FunctionDefinition, FunctionNode, FunctionEdge,
    DataType, NodeType,
)


def validate_function_graph(func_def: FunctionDefinition) -> list:
    """Validate function graph. Returns list of error messages."""
    errors = []
    nodes_by_id = {n.node_id: n for n in func_def.nodes}

    # 1) Exactly 1 Start node
    start_nodes = [n for n in func_def.nodes if n.node_type == NodeType.START]
    if len(start_nodes) == 0:
        errors.append("Start node is required")
    elif len(start_nodes) > 1:
        errors.append("Only 1 Start node allowed")

    # 2) At least 1 End node
    end_nodes = [n for n in func_def.nodes if n.node_type == NodeType.END]
    if len(end_nodes) == 0:
        errors.append("At least 1 End node required")

    # 3) Start must have outgoing exec edge
    if start_nodes:
        start_id = start_nodes[0].node_id
        has_exec_out = any(
            e.source_node_id == start_id and e.edge_type == "exec"
            for e in func_def.edges
        )
        if not has_exec_out:
            errors.append("Start node needs an exec connection")

    # 4) End must have incoming exec edge
    for end_node in end_nodes:
        has_exec_in = any(
            e.target_node_id == end_node.node_id and e.edge_type == "exec"
            for e in func_def.edges
        )
        if not has_exec_in:
            errors.append("End node needs an exec connection")

    # 5) Edge references valid nodes
    for e in func_def.edges:
        if e.source_node_id not in nodes_by_id:
            errors.append("Edge references missing source node")
        if e.target_node_id not in nodes_by_id:
            errors.append("Edge references missing target node")

    # 6) Cycle detection (exclude loop_body edges)
    loop_exec_ports = {"loop_body"}
    adj = {n.node_id: [] for n in func_def.nodes}
    in_degree = {n.node_id: 0 for n in func_def.nodes}
    for e in func_def.edges:
        if e.edge_type == "exec" and e.source_port_id not in loop_exec_ports:
            if e.source_node_id in nodes_by_id and e.target_node_id in nodes_by_id:
                adj[e.source_node_id].append(e.target_node_id)
                in_degree[e.target_node_id] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        nid = queue.pop(0)
        visited += 1
        for target in adj[nid]:
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)

    if visited != len(func_def.nodes):
        errors.append("Graph has a cycle")

    # 7) LLM Call: model + prompt required
    for n in func_def.nodes:
        if n.node_type == NodeType.LLM_CALL:
            if not n.config.get("model"):
                errors.append("LLM Call node needs a model")
            if not n.config.get("prompt_template"):
                errors.append("LLM Call node needs a prompt")

    return errors


# ──────────────────────────────────────────
# Pure node type set
# ──────────────────────────────────────────

_PURE_NODE_TYPES = {
    NodeType.PROMPT_BUILDER, NodeType.MATH, NodeType.COMPARE,
    NodeType.STRING_OP, NodeType.ARRAY_OP, NodeType.JSON_PARSE,
    NodeType.JSON_PATH, NodeType.TYPE_CONVERT,
    NodeType.GET_VARIABLE, NodeType.MAKE_LITERAL,
}


class BlueprintExecutionWorker(QThread):
    """Blueprint-style dual-flow execution engine"""

    step_started = pyqtSignal(str, int, int)     # (node_name, step, total)
    all_finished = pyqtSignal(dict, list)        # ({output_name: value}, images)
    error_signal = pyqtSignal(str)               # error message
    tokens_received = pyqtSignal(int, int)       # (in, out) tokens

    MAX_LOOP_ITERATIONS = 100
    MAX_TOTAL_STEPS = 500

    def __init__(self, provider: GeminiProvider, func_def: FunctionDefinition,
                 initial_input: str, context_messages: list = None,
                 system_prompt: str = "", system_files: list = None,
                 parameters: dict = None, node_options: dict = None):
        super().__init__()
        self.provider = provider
        self.func_def = func_def
        self.initial_input = initial_input
        self.context_messages = context_messages or []
        self.system_prompt = system_prompt
        self.system_files = system_files or []
        self.parameters = parameters or {}
        self.node_options = node_options or {}
        self._cancel = False

        self.variables = {}
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self._generated_images = []
        self._outputs = {}

        # Internal maps built in run()
        self._nodes_by_id = {}
        self._exec_edges = {}      # node_id -> {port_id: target_node_id}
        self._data_edges_in = {}   # (target_node_id, target_port_id) -> (source_node_id, source_port_id)
        self._node_outputs = {}    # node_id -> {port_id: value}
        self._node_cache = {}      # Pure node cache: node_id -> {port_id: value}
        self._step_count = 0
        self._resolve_visited: set | None = None  # F2: 순환 참조 감지용

    def cancel(self):
        self._cancel = True

    def run(self):
        errors = validate_function_graph(self.func_def)
        if errors:
            self.error_signal.emit("Graph: " + "; ".join(errors))
            return
        try:
            self._build_maps()
            result = self._execute_graph()
            if result is not None:
                self.all_finished.emit(result, self._generated_images)
        except Exception as e:
            self.error_signal.emit(str(e))

    def _build_maps(self):
        """Build edge lookup maps"""
        self._nodes_by_id = {n.node_id: n for n in self.func_def.nodes}

        for e in self.func_def.edges:
            if e.edge_type == "exec":
                self._exec_edges.setdefault(e.source_node_id, {})[e.source_port_id] = e.target_node_id
            else:  # data
                self._data_edges_in[(e.target_node_id, e.target_port_id)] = (e.source_node_id, e.source_port_id)

        # Initialize parameter values in node outputs for Start node
        for n in self.func_def.nodes:
            if n.node_type == NodeType.START:
                outputs = {}
                for param in n.config.get("parameters", []):
                    param_name = param["name"]
                    param_info = self.parameters.get(param_name, {})
                    outputs[param_name] = param_info.get("value", "")
                self._node_outputs[n.node_id] = outputs

    def _execute_graph(self) -> dict | None:
        """Main execution: follow exec chain from Start"""
        start_node = next(
            (n for n in self.func_def.nodes if n.node_type == NodeType.START), None
        )
        if not start_node:
            self.error_signal.emit("No Start node")
            return None

        total = len(self.func_def.nodes)
        self._step_count = 0

        # Follow exec chain from Start
        next_id = self._exec_edges.get(start_node.node_id, {}).get("exec_out")
        while next_id and self._step_count < self.MAX_TOTAL_STEPS:
            if self._cancel:
                return None

            node = self._nodes_by_id.get(next_id)
            if not node:
                self.error_signal.emit(f"Node not found: {next_id}")
                return None

            self._step_count += 1
            self.step_started.emit(self._display_name(node), self._step_count, total)

            next_id = self._execute_impure_node(node)

        if self._step_count >= self.MAX_TOTAL_STEPS:
            self.error_signal.emit(f"Max steps exceeded ({self.MAX_TOTAL_STEPS})")
            return None

        return self._outputs if self._outputs else {"output": ""}

    def _execute_impure_node(self, node: FunctionNode) -> str | None:
        """Execute an impure node and return next exec target node_id, or None"""
        t = node.node_type

        if t == NodeType.END:
            # Collect result from data input
            result = self._resolve_data_input(node.node_id, "result")
            output_name = node.config.get("output_name", "output")
            self._outputs[output_name] = result if result is not None else ""
            return None

        elif t == NodeType.LLM_CALL:
            return self._exec_llm_call(node)

        elif t == NodeType.BRANCH:
            return self._exec_branch(node)

        elif t == NodeType.SWITCH:
            return self._exec_switch(node)

        elif t == NodeType.FOR_EACH:
            return self._exec_for_each(node)

        elif t == NodeType.WHILE_LOOP:
            return self._exec_while_loop(node)

        elif t == NodeType.SEQUENCE:
            return self._exec_sequence(node)

        elif t == NodeType.RESPONSE_PARSER:
            return self._exec_response_parser(node)

        elif t == NodeType.IMAGE_GENERATOR:
            return self._exec_image_generator(node)

        elif t == NodeType.SET_VARIABLE:
            return self._exec_set_variable(node)

        # Unknown impure node - try to follow exec_out
        return self._exec_edges.get(node.node_id, {}).get("exec_out")

    # ──────────────────────────────────────────
    # Data resolution
    # ──────────────────────────────────────────

    def _resolve_data_input(self, node_id: str, port_id: str):
        """Walk data pins backward to resolve a value.
        F2: _resolve_visited로 순환 참조 감지."""
        key = (node_id, port_id)
        if key not in self._data_edges_in:
            return None

        # F2: 순환 참조 감지
        is_root = self._resolve_visited is None
        if is_root:
            self._resolve_visited = set()
        try:
            if key in self._resolve_visited:
                return None  # 순환 감지 — 무한 재귀 방지
            self._resolve_visited.add(key)

            src_node_id, src_port_id = self._data_edges_in[key]
            src_node = self._nodes_by_id.get(src_node_id)
            if not src_node:
                return None

            if src_node.node_type in _PURE_NODE_TYPES:
                # Pure node: evaluate lazily with caching
                outputs = self._evaluate_pure_node(src_node)
                return outputs.get(src_port_id)
            else:
                # Impure node: use last stored output
                outputs = self._node_outputs.get(src_node_id, {})
                return outputs.get(src_port_id)
        finally:
            if is_root:
                self._resolve_visited = None

    def _evaluate_pure_node(self, node: FunctionNode) -> dict:
        """Evaluate a pure node (cached per execution cycle)"""
        if node.node_id in self._node_cache:
            return self._node_cache[node.node_id]

        t = node.node_type
        result = {}

        if t == NodeType.MATH:
            result = self._eval_math(node)
        elif t == NodeType.COMPARE:
            result = self._eval_compare(node)
        elif t == NodeType.STRING_OP:
            result = self._eval_string_op(node)
        elif t == NodeType.ARRAY_OP:
            result = self._eval_array_op(node)
        elif t == NodeType.JSON_PARSE:
            result = self._eval_json_parse(node)
        elif t == NodeType.JSON_PATH:
            result = self._eval_json_path(node)
        elif t == NodeType.TYPE_CONVERT:
            result = self._eval_type_convert(node)
        elif t == NodeType.PROMPT_BUILDER:
            result = self._eval_prompt_builder(node)
        elif t == NodeType.GET_VARIABLE:
            var_name = node.config.get("var_name", "")
            result = {"value": self.variables.get(var_name, "")}
        elif t == NodeType.MAKE_LITERAL:
            result = {"value": self._get_literal_value(node)}

        self._node_cache[node.node_id] = result
        return result

    # ──────────────────────────────────────────
    # Control Flow execution
    # ──────────────────────────────────────────

    def _exec_branch(self, node: FunctionNode) -> str | None:
        condition = self._resolve_data_input(node.node_id, "condition")
        branch = "true" if self._to_bool(condition) else "false"
        return self._exec_edges.get(node.node_id, {}).get(branch)

    def _exec_switch(self, node: FunctionNode) -> str | None:
        value = self._resolve_data_input(node.node_id, "value")
        value_str = str(value) if value is not None else ""
        cases = node.config.get("cases", [])
        for i, case_val in enumerate(cases):
            if value_str == case_val:
                return self._exec_edges.get(node.node_id, {}).get(f"case_{i}")
        return self._exec_edges.get(node.node_id, {}).get("default")

    def _exec_for_each(self, node: FunctionNode) -> str | None:
        array = self._resolve_data_input(node.node_id, "array")
        if not isinstance(array, (list, tuple)):
            try:
                array = json.loads(str(array)) if array else []
            except (json.JSONDecodeError, TypeError):
                array = []

        body_target = self._exec_edges.get(node.node_id, {}).get("loop_body")
        done_target = self._exec_edges.get(node.node_id, {}).get("completed")
        max_iter = min(node.config.get("max_iter", 100), self.MAX_LOOP_ITERATIONS)

        if not body_target:
            return done_target

        for i, element in enumerate(array[:max_iter]):
            if self._cancel:
                return None
            if self._step_count >= self.MAX_TOTAL_STEPS:
                self.error_signal.emit(f"Max steps exceeded ({self.MAX_TOTAL_STEPS})")
                return None

            # Set loop outputs
            self._node_outputs[node.node_id] = {"element": element, "index": i}
            self._node_cache.clear()  # Clear pure cache per iteration

            # Execute loop body chain
            self._execute_exec_chain(body_target, node.node_id)

            # F3: 루프 본체 실행 후 step count 재검사
            if self._step_count >= self.MAX_TOTAL_STEPS:
                self.error_signal.emit(f"Max steps exceeded ({self.MAX_TOTAL_STEPS})")
                return None

        return done_target

    def _exec_while_loop(self, node: FunctionNode) -> str | None:
        body_target = self._exec_edges.get(node.node_id, {}).get("loop_body")
        done_target = self._exec_edges.get(node.node_id, {}).get("completed")
        max_iter = min(node.config.get("max_iter", 100), self.MAX_LOOP_ITERATIONS)

        if not body_target:
            return done_target

        for i in range(max_iter):
            if self._cancel:
                return None
            if self._step_count >= self.MAX_TOTAL_STEPS:
                self.error_signal.emit(f"Max steps exceeded ({self.MAX_TOTAL_STEPS})")
                return None

            condition = self._resolve_data_input(node.node_id, "condition")
            if not self._to_bool(condition):
                break

            self._node_outputs[node.node_id] = {"index": i}
            self._node_cache.clear()

            self._execute_exec_chain(body_target, node.node_id)

            # F3: 루프 본체 실행 후 step count 재검사
            if self._step_count >= self.MAX_TOTAL_STEPS:
                self.error_signal.emit(f"Max steps exceeded ({self.MAX_TOTAL_STEPS})")
                return None

        return done_target

    def _exec_sequence(self, node: FunctionNode) -> str | None:
        """F1: Sequence 노드 — 모든 분기 실행 후 exec_out으로 계속"""
        count = node.config.get("output_count", 2)
        for i in range(count):
            target = self._exec_edges.get(node.node_id, {}).get(f"then_{i}")
            if target:
                self._execute_exec_chain(target, None)
            if self._cancel:
                return None

        # F1: 모든 분기 완료 후 메인 체인으로 이어짐
        return self._exec_edges.get(node.node_id, {}).get("exec_out")

    def _execute_exec_chain(self, start_node_id: str, stop_at_node_id: str | None):
        """Execute a chain of impure nodes until end or stop_at_node.
        F6: 예외 발생 시 error_signal 발신 (QThread 크래시 방지)."""
        current_id = start_node_id
        while current_id and current_id != stop_at_node_id:
            if self._cancel:
                return
            if self._step_count >= self.MAX_TOTAL_STEPS:
                return

            node = self._nodes_by_id.get(current_id)
            if not node:
                return
            if node.node_type == NodeType.END:
                # End node in sub-chain: collect output
                result = self._resolve_data_input(node.node_id, "result")
                output_name = node.config.get("output_name", "output")
                self._outputs[output_name] = result if result is not None else ""
                return

            self._step_count += 1
            total = len(self.func_def.nodes)
            self.step_started.emit(self._display_name(node), self._step_count, total)
            try:
                current_id = self._execute_impure_node(node)
            except Exception as e:
                self.error_signal.emit(f"Sub-chain error at {self._display_name(node)}: {e}")
                return

    # ──────────────────────────────────────────
    # AI node execution
    # ──────────────────────────────────────────

    def _exec_llm_call(self, node: FunctionNode) -> str | None:
        model = node.config.get("model", "")
        template = node.config.get("prompt_template", "{input}")

        # Resolve all data inputs
        input_values = {}
        num_args = node.config.get("_num_arg_ports", 1)
        for i in range(num_args):
            val = self._resolve_data_input(node.node_id, f"in_{i}")
            if val is not None:
                input_values[f"in_{i}"] = val

        # Build prompt from template
        prompt = template.replace("{input}", self.initial_input)
        for key, val in input_values.items():
            prompt = prompt.replace(f"{{{key}}}", str(val))
        # Variable substitution
        for var_name, var_value in self.variables.items():
            prompt = prompt.replace(f"{{var:{var_name}}}", str(var_value))
        # Parameter substitution
        for param_name, param_info in self.parameters.items():
            if param_info.get("type") != "image":
                prompt = prompt.replace(f"{{param:{param_name}}}", str(param_info.get("value", "")))
        # Clean unresolved
        prompt = re.sub(r'\{(?:in_\d+|var:[^}]*|param:[^}]*)\}', '', prompt)

        # Collect image attachments
        image_attachments = []
        for param_info in self.parameters.values():
            if param_info.get("type") == "image":
                path = param_info.get("value", "")
                if path and os.path.exists(path):
                    image_attachments.append(path)

        messages = list(self.context_messages) + [
            ChatMessage(role="user", content=prompt,
                        attachments=image_attachments or None)
        ]

        model_opts = get_default_options(model)
        model_opts.update(self.node_options)

        try:
            result = self.provider.chat(
                model, messages, stream=False,
                system_prompt=self.system_prompt,
                system_files=self.system_files,
                **model_opts,
            )
        except Exception as e:
            # F5: LLM 에러를 error_signal로도 발신 (무음 처리 방지)
            text = f"[LLM Error] {e}"
            self.error_signal.emit(text)
            self._node_outputs[node.node_id] = {"response": text}
            return self._exec_edges.get(node.node_id, {}).get("exec_out")

        if isinstance(result, dict):
            text = result.get("text", "")
            self.total_tokens_in += result.get("prompt_tokens") or 0
            self.total_tokens_out += result.get("candidates_tokens") or 0
            for img_data in result.get("images", []):
                self._generated_images.append(img_data)
        elif isinstance(result, str):
            text = result
        else:
            text = ""
            for chunk in result:
                if isinstance(chunk, dict) and chunk.get("__usage__"):
                    self.total_tokens_in += chunk.get("prompt_tokens") or 0
                    self.total_tokens_out += chunk.get("candidates_tokens") or 0
                elif isinstance(chunk, str):
                    text += chunk

        self.tokens_received.emit(self.total_tokens_in, self.total_tokens_out)
        self._node_outputs[node.node_id] = {"response": text}
        return self._exec_edges.get(node.node_id, {}).get("exec_out")

    def _exec_response_parser(self, node: FunctionNode) -> str | None:
        text = self._resolve_data_input(node.node_id, "text")
        pattern = self._resolve_data_input(node.node_id, "pattern")
        text = str(text) if text is not None else ""
        pattern = str(pattern) if pattern is not None else ""
        mode = node.config.get("mode", "json")

        parsed = None
        items = []

        if mode == "json":
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    items = parsed
                elif isinstance(parsed, dict):
                    items = list(parsed.values())
            except json.JSONDecodeError:
                parsed = text
        elif mode == "regex":
            try:
                matches = re.findall(pattern, text)
                items = matches
                parsed = matches[0] if matches else ""
            except re.error:
                parsed = text
        elif mode == "split":
            delimiter = pattern or "\n"
            items = [s.strip() for s in text.split(delimiter) if s.strip()]
            parsed = items[0] if items else ""

        self._node_outputs[node.node_id] = {"parsed": parsed, "items": items}
        return self._exec_edges.get(node.node_id, {}).get("exec_out")

    def _exec_image_generator(self, node: FunctionNode) -> str | None:
        prompt = self._resolve_data_input(node.node_id, "prompt")
        prompt = str(prompt) if prompt is not None else ""
        model = node.config.get("model", "")
        aspect_ratio = node.config.get("aspect_ratio", "1:1")

        messages = [ChatMessage(role="user", content=prompt)]
        model_opts = get_default_options(model)
        model_opts["aspect_ratio"] = aspect_ratio

        image_result = None
        try:
            result = self.provider.chat(model, messages, stream=False, **model_opts)
            if isinstance(result, dict):
                images = result.get("images", [])
                if images:
                    image_result = images[0]
                    self._generated_images.extend(images)
        except Exception:
            pass

        self._node_outputs[node.node_id] = {"image": image_result}
        return self._exec_edges.get(node.node_id, {}).get("exec_out")

    def _exec_set_variable(self, node: FunctionNode) -> str | None:
        var_name = node.config.get("var_name", "")
        value = self._resolve_data_input(node.node_id, "value")
        if var_name:
            self.variables[var_name] = value
        self._node_outputs[node.node_id] = {"value": value}
        return self._exec_edges.get(node.node_id, {}).get("exec_out")

    # ──────────────────────────────────────────
    # Pure node evaluators
    # ──────────────────────────────────────────

    def _eval_math(self, node: FunctionNode) -> dict:
        a = self._to_number(self._resolve_data_input(node.node_id, "a"))
        b = self._to_number(self._resolve_data_input(node.node_id, "b"))
        op = node.config.get("op", "+")

        try:
            if op == "+": result = a + b
            elif op == "-": result = a - b
            elif op == "*": result = a * b
            elif op == "/": result = a / b if b != 0 else 0
            elif op == "%": result = a % b if b != 0 else 0
            elif op == "pow": result = a ** b
            elif op == "min": result = min(a, b)
            elif op == "max": result = max(a, b)
            else: result = a + b
        except Exception:
            result = 0

        return {"result": result}

    def _eval_compare(self, node: FunctionNode) -> dict:
        a = self._resolve_data_input(node.node_id, "a")
        b = self._resolve_data_input(node.node_id, "b")
        op = node.config.get("op", "==")

        a_str = str(a) if a is not None else ""
        b_str = str(b) if b is not None else ""

        try:
            if op == "==": result = a_str == b_str
            elif op == "!=": result = a_str != b_str
            elif op == "<": result = self._to_number(a) < self._to_number(b)
            elif op == ">": result = self._to_number(a) > self._to_number(b)
            elif op == "<=": result = self._to_number(a) <= self._to_number(b)
            elif op == ">=": result = self._to_number(a) >= self._to_number(b)
            elif op == "contains": result = b_str.lower() in a_str.lower()
            elif op == "starts_with": result = a_str.lower().startswith(b_str.lower())
            elif op == "ends_with": result = a_str.lower().endswith(b_str.lower())
            else: result = a_str == b_str
        except Exception:
            result = False

        return {"result": result}

    def _eval_string_op(self, node: FunctionNode) -> dict:
        text = str(self._resolve_data_input(node.node_id, "text") or "")
        param = str(self._resolve_data_input(node.node_id, "param") or "")
        op = node.config.get("op", "trim")

        if op == "replace":
            parts = param.split("->", 1)
            old = parts[0] if parts else ""
            new = parts[1] if len(parts) > 1 else ""
            result = text.replace(old, new)
        elif op == "split":
            result = text.split(param) if param else text.split()
        elif op == "join":
            if isinstance(text, list):
                result = param.join(str(x) for x in text)
            else:
                result = param.join(text.split("\n"))
        elif op == "trim": result = text.strip()
        elif op == "upper": result = text.upper()
        elif op == "lower": result = text.lower()
        elif op == "format": result = param.replace("{text}", text)
        elif op == "regex":
            try:
                matches = re.findall(param, text)
                result = matches[0] if matches else ""
            except re.error:
                result = ""
        elif op == "substring":
            try:
                parts = param.split(",")
                start = int(parts[0]) if parts else 0
                end = int(parts[1]) if len(parts) > 1 else len(text)
                result = text[start:end]
            except (ValueError, IndexError):
                result = text
        elif op == "length":
            result = str(len(text))
        else:
            result = text

        return {"result": result}

    def _eval_array_op(self, node: FunctionNode) -> dict:
        array = self._resolve_data_input(node.node_id, "array")
        item = self._resolve_data_input(node.node_id, "item")
        op = node.config.get("op", "push")

        if not isinstance(array, list):
            try:
                array = json.loads(str(array)) if array else []
            except (json.JSONDecodeError, TypeError):
                array = []

        array = list(array)  # copy
        element = None

        if op == "push":
            array.append(item)
        elif op == "pop":
            element = array.pop() if array else None
        elif op == "length":
            element = len(array)
        elif op == "find":
            element = next((x for x in array if x == item), None)
        elif op == "filter":
            array = [x for x in array if x is not None and str(x).strip()]
        elif op == "slice":
            try:
                parts = str(item).split(",") if item else ["0"]
                start = int(parts[0])
                end = int(parts[1]) if len(parts) > 1 else len(array)
                array = array[start:end]
            except (ValueError, IndexError):
                pass
        elif op == "sort":
            try:
                array.sort(key=str)
            except Exception:
                pass
        elif op == "reverse":
            array.reverse()
        elif op == "flatten":
            flat = []
            for x in array:
                if isinstance(x, list):
                    flat.extend(x)
                else:
                    flat.append(x)
            array = flat

        return {"result": array, "element": element}

    def _eval_json_parse(self, node: FunctionNode) -> dict:
        text = str(self._resolve_data_input(node.node_id, "text") or "")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = {}
        return {"object": obj}

    def _eval_json_path(self, node: FunctionNode) -> dict:
        obj = self._resolve_data_input(node.node_id, "object")
        path = self._resolve_data_input(node.node_id, "path")
        if path is None:
            path = node.config.get("default_path", "")
        path = str(path)

        if not isinstance(obj, (dict, list)):
            try:
                obj = json.loads(str(obj)) if obj else {}
            except (json.JSONDecodeError, TypeError):
                obj = {}

        # Simple dot-notation path: "key1.key2.0.key3"
        value = obj
        if path:
            for part in path.split("."):
                if not part:
                    continue
                if isinstance(value, dict):
                    value = value.get(part)
                elif isinstance(value, list):
                    try:
                        value = value[int(part)]
                    except (ValueError, IndexError):
                        value = None
                        break
                else:
                    value = None
                    break

        return {"value": value}

    def _eval_type_convert(self, node: FunctionNode) -> dict:
        input_val = self._resolve_data_input(node.node_id, "input")
        target = node.config.get("target_type", "string")

        try:
            if target == "string":
                result = str(input_val) if input_val is not None else ""
            elif target == "number":
                result = self._to_number(input_val)
            elif target == "boolean":
                result = self._to_bool(input_val)
            elif target == "array":
                if isinstance(input_val, list):
                    result = input_val
                elif isinstance(input_val, str):
                    try:
                        result = json.loads(input_val)
                        if not isinstance(result, list):
                            result = [result]
                    except json.JSONDecodeError:
                        result = [input_val]
                else:
                    result = [input_val]
            elif target == "object":
                if isinstance(input_val, dict):
                    result = input_val
                elif isinstance(input_val, str):
                    try:
                        result = json.loads(input_val)
                    except json.JSONDecodeError:
                        result = {"value": input_val}
                else:
                    result = {"value": input_val}
            else:
                result = input_val
        except Exception:
            result = input_val

        return {"output": result}

    def _eval_prompt_builder(self, node: FunctionNode) -> dict:
        system = str(self._resolve_data_input(node.node_id, "system") or "")
        user = str(self._resolve_data_input(node.node_id, "user") or "")
        context = str(self._resolve_data_input(node.node_id, "context") or "")
        template = node.config.get("template", "{system}\n\n{user}\n\n{context}")

        prompt = template.replace("{system}", system)
        prompt = prompt.replace("{user}", user)
        prompt = prompt.replace("{context}", context)
        return {"prompt": prompt.strip()}

    # ──────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────

    def _get_literal_value(self, node: FunctionNode):
        val_type = node.config.get("type", "string")
        raw = node.config.get("value", "")
        if val_type == "number":
            return self._to_number(raw)
        elif val_type == "boolean":
            return self._to_bool(raw)
        elif val_type == "array":
            try:
                return json.loads(str(raw)) if raw else []
            except json.JSONDecodeError:
                return []
        elif val_type == "object":
            try:
                return json.loads(str(raw)) if raw else {}
            except json.JSONDecodeError:
                return {}
        return str(raw)

    @staticmethod
    def _to_number(val) -> float:
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return float(str(val))
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _to_bool(val) -> bool:
        if val is None:
            return False
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return val != 0
        s = str(val).strip().lower()
        return s not in ("", "0", "false", "no", "null", "none")

    def _display_name(self, node: FunctionNode) -> str:
        names = {
            NodeType.START: "Start",
            NodeType.END: "End",
            NodeType.BRANCH: "Branch",
            NodeType.SWITCH: "Switch",
            NodeType.FOR_EACH: "ForEach",
            NodeType.WHILE_LOOP: "While",
            NodeType.SEQUENCE: "Sequence",
            NodeType.LLM_CALL: "LLM Call",
            NodeType.PROMPT_BUILDER: "Prompt Builder",
            NodeType.RESPONSE_PARSER: "Response Parser",
            NodeType.IMAGE_GENERATOR: "Image Gen",
            NodeType.MATH: "Math",
            NodeType.COMPARE: "Compare",
            NodeType.STRING_OP: "String",
            NodeType.ARRAY_OP: "Array",
            NodeType.JSON_PARSE: "JSON Parse",
            NodeType.JSON_PATH: "JSON Path",
            NodeType.TYPE_CONVERT: "Type Convert",
            NodeType.GET_VARIABLE: "Get Var",
            NodeType.SET_VARIABLE: "Set Var",
            NodeType.MAKE_LITERAL: "Literal",
        }
        return names.get(node.node_type, node.node_type)


# Keep old name as alias for backward compatibility in plugin.py imports
FunctionExecutionWorker = BlueprintExecutionWorker
