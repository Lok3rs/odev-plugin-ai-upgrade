"""Upgrade Agent module."""

import ast
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import litellm
from lxml import etree

from odev.common.logging import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an Expert Odoo Developer. Your objective is to upgrade an Odoo module from a previous version to a new version.
You have access to several tools to read files, edit files, and run tests.

Process:
1. Examine the module structure and identify files that need updates (e.g. models, views, manifest).
2. Edit files one by one to apply known Odoo version migrations. Replace old API calls or outdated XML attributes.
3. Validate your XML files using `validate_xml` to catch syntax errors before running full tests.
4. If you are stuck or need to understand how the new Odoo version handles a feature, use `search_odoo_source` or `get_odoo_file_diff`.
5. Run the tests using `run_odoo_tests` to verify your changes against the target version.
6. Analyze test failures (tracebacks) and use `edit_file` to fix them.
7. Repeat testing and fixing until tests pass or you reach the maximum iterations.
8. NEVER guess Odoo core source changes if you can check them.

When editing a file:
- Ensure the `old_content` exactly matches a contiguous block of text in the file.
- Keep the indentation correct in the `new_content`.
"""

class UpgradeAgent:
    """Agent that orchestrates the upgrade process using litellm and tool calling."""

    def __init__(self, llm: Any, module_name: str, module_path: Path, from_version: str, target_version: Any, odev_app: Any, console: Any):
        self.llm = llm
        self.module_name = module_name
        self.module_path = module_path
        self.from_version = from_version
        self.target_version = target_version
        self.odev = odev_app
        self.console = console
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": f"Please upgrade the module '{self.module_name}' from Odoo {self.from_version} to {self.target_version}. The module is located at {self.module_path}"}
        ]

    def _get_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Reads the content of a file within the module.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string", "description": "Relative path to the file inside the module (e.g. '__manifest__.py')"}
                        },
                        "required": ["filepath"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Replaces a specific block of text in a file with new text.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string", "description": "Relative path to the file inside the module"},
                            "old_content": {"type": "string", "description": "The exact contiguous block of text to replace. Must match the file exactly."},
                            "new_content": {"type": "string", "description": "The new content to insert in place of old_content."}
                        },
                        "required": ["filepath", "old_content", "new_content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "validate_xml",
                    "description": "Validates an XML file's syntax to ensure it is well-formed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string", "description": "Relative path to the XML file"}
                        },
                        "required": ["filepath"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_odoo_tests",
                    "description": "Spins up a temporary DB and runs the module's tests against the target Odoo version. Use this to verify your changes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "test_tags": {"type": "string", "description": "Optional test tags (e.g., '/my_module')"}
                        },
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_odoo_source",
                    "description": "Greps the Odoo source code for a specific version.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The search query (grep pattern)"},
                            "version": {"type": "string", "description": "The Odoo version to search in (e.g., '16.0' or '17.0')"}
                        },
                        "required": ["query", "version"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_odoo_file_diff",
                    "description": "Gets the git diff of a core Odoo file between the source version and target version.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filepath": {"type": "string", "description": "Relative path in the odoo core repository (e.g., 'odoo/models.py')"},
                            "source_version": {"type": "string", "description": "Source Odoo version"},
                            "target_version": {"type": "string", "description": "Target Odoo version"}
                        },
                        "required": ["filepath", "source_version", "target_version"]
                    }
                }
            }
        ]

    def _execute_tool(self, name: str, args: dict) -> str:
        try:
            if name == "read_file":
                return self.tool_read_file(args.get("filepath"))
            elif name == "edit_file":
                return self.tool_edit_file(args.get("filepath"), args.get("old_content"), args.get("new_content"))
            elif name == "validate_xml":
                return self.tool_validate_xml(args.get("filepath"))
            elif name == "run_odoo_tests":
                return self.tool_run_odoo_tests(args.get("test_tags"))
            elif name == "search_odoo_source":
                return self.tool_search_odoo_source(args.get("query"), args.get("version"))
            elif name == "get_odoo_file_diff":
                return self.tool_get_odoo_file_diff(args.get("filepath"), args.get("source_version"), args.get("target_version"))
            else:
                return f"Error: Tool '{name}' not found."
        except Exception as e:
            return f"Error executing '{name}': {e}"

    def run_upgrade_loop(self, max_iterations: int = 10) -> bool:
        """Main interaction loop with the LLM."""
        # Get the first available model from the LLM configuration wrapper
        model_name = self.llm._get_model_list()[0] if self.llm._get_model_list() else "gemini/gemini-pro"
        
        for iteration in range(max_iterations):
            logger.info(f"--- Iteration {iteration + 1}/{max_iterations} ---")
            
            try:
                response = litellm.completion(
                    model=model_name,
                    messages=self.messages,
                    tools=self._get_tools(),
                    tool_choice="auto"
                )
            except Exception as e:
                logger.error(f"LiteLLM completion error: {e}")
                return False

            message = response.choices[0].message
            self.messages.append(message.model_dump(exclude_none=True))

            if message.tool_calls:
                for tool_call in message.tool_calls:
                    function_name = tool_call.function.name
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except Exception:
                        arguments = {}
                    
                    logger.info(f"Agent called tool: {function_name}({arguments})")
                    
                    # Tool execution
                    tool_result = self._execute_tool(function_name, arguments)
                    
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": str(tool_result)[:4000] # truncate very long outputs
                    })
                    
                    if function_name == "run_odoo_tests":
                        # If tests pass successfully, we might consider the upgrade done
                        if "Tests passed successfully" in tool_result:
                            logger.info("Agent ran tests and they passed!")
                            return True
            else:
                logger.info("Agent provided a direct response:")
                self.console.print(message.content)
                if "success" in str(message.content).lower() or "finished" in str(message.content).lower():
                    return True
                
        return False

    # --- Tool Implementations ---

    def tool_read_file(self, filepath: str) -> str:
        path = self.module_path / filepath
        if not path.is_file():
            return f"Error: File '{filepath}' does not exist inside {self.module_name}."
        return path.read_text(encoding="utf-8")

    def tool_edit_file(self, filepath: str, old_content: str, new_content: str) -> str:
        path = self.module_path / filepath
        if not path.is_file():
            # If the filepath doesn't exist, we can treat it as a file creation if old_content is empty
            if not old_content:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_content, encoding="utf-8")
                return f"Successfully created {filepath}."
            return f"Error: File '{filepath}' does not exist."
            
        content = path.read_text(encoding="utf-8")
        if old_content not in content:
            return "Error: old_content not found exactly as provided in the file. Check whitespace and indentation."
        
        content = content.replace(old_content, new_content, 1)
        path.write_text(content, encoding="utf-8")
        return f"Successfully updated {filepath}."

    def tool_validate_xml(self, filepath: str) -> str:
        path = self.module_path / filepath
        if not path.is_file():
            return f"Error: File '{filepath}' does not exist."
        try:
            etree.parse(str(path))
            return "XML is well-formed."
        except etree.XMLSyntaxError as e:
            return f"XML Syntax Error: {e}"

    def tool_run_odoo_tests(self, test_tags: str = None) -> str:
        # Wrap the `odev test` command logic here.
        args_list = ["--target-version", str(self.target_version), "-i", self.module_name]
        if test_tags:
            args_list.extend(["--test-tags", test_tags])
            
        try:
            # We capture output using command execution or subprocess for safe ephemeral run.
            # We run the equivalent of `odev test <module> --target-version <X>`
            # using odev internal run_command.
            with self.odev.console.capture() as capture:
                success = False
                try:
                    self.odev.run_command("test", self.module_name, *args_list)
                    success = True
                except Exception as e:
                    success = False
                    
            output = capture.get()
            if success:
                return f"Tests passed successfully.\n\nLogs:\n{output[-2000:]}"
            else:
                return f"Tests failed.\n\nLogs:\n{output[-4000:]}"
        except Exception as e:
            return f"Failed to execute tests: {e}"

    def tool_search_odoo_source(self, query: str, version: str) -> str:
        # Implementation to grep inside the local Odoo source for `version`
        # In odev, sources are usually located at `~/odoo/repositories/odoo` or similar 
        # based on user config. We can use the OdoobinProcess or just odev config to find the path.
        # Fallback to standard git grep if we know the path.
        return "Not implemented directly. Needs odoobin path resolution."

    def tool_get_odoo_file_diff(self, filepath: str, source_version: str, target_version: str) -> str:
        # Diff a specific file between two versions
        return "Not implemented directly. Needs git diff capabilities on odoo source."
