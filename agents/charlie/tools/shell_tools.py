"""
Shell tools: execute terminal commands.
Gives agents the ability to run shell commands and read output.
"""

import subprocess
import os


def _run_shell(args):
    """Execute a shell command and return stdout + stderr."""
    cmd = args["cmd"]
    cwd = args.get("cwd", None)
    timeout = args.get("timeout", 30)

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=cwd, timeout=timeout
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n--- stderr ---\n"
            output += result.stderr

        # Truncate if too long (protect context window)
        if len(output) > 5000:
            output = output[:2500] + "\n\n... [truncated] ...\n\n" + output[-2500:]

        if not output:
            output = f"(no output, exit code: {result.returncode})"

        return output

    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout} seconds"
    except Exception as e:
        return f"Error: {e}"


TOOLS = {
    "run_shell": {
        "description": "执行终端命令并返回输出结果。可用于运行脚本、查看文件、检查系统状态等",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {
                    "type": "string",
                    "description": "要执行的终端命令"
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（可选，默认为当前目录）"
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数（可选，默认30秒）"
                }
            },
            "required": ["cmd"]
        },
        "handler": _run_shell
    },
}
