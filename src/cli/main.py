"""CLI entry point for the agent orchestrator."""

import argparse
import json
import sys

from src.common.config import Config
from src.common.logging import configure_logging

VALID_OUTPUT_MODES = ("text", "json", "table")


def format_output(data, mode: str):
    """Format data according to the output mode."""
    if mode == "json":
        return json.dumps(data, indent=2)
    elif mode == "table":
        if isinstance(data, dict):
            lines = []
            for k, v in data.items():
                lines.append(f"{k:<20} {v}")
            return "\n".join(lines)
        elif isinstance(data, list):
            return "\n".join(str(item) for item in data)
        return str(data)
    else:
        return str(data)


def cli(argv=None):
    # Parent parser with shared arguments
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", "-c", help="Path to config file")
    parent.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    parent.add_argument(
        "--output", "-o",
        choices=VALID_OUTPUT_MODES,
        default="text",
        help="Output mode: text, json, or table (default: text)",
    )

    parser = argparse.ArgumentParser(
        description="Agent Orchestrator CLI",
        parents=[parent],
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    init_parser = subparsers.add_parser("init", parents=[parent], help="Initialize a new project")
    init_parser.add_argument("name", help="Project name")

    deploy_parser = subparsers.add_parser("deploy", parents=[parent], help="Deploy an agent")
    deploy_parser.add_argument("manifest", help="Path to agent manifest file")

    status_parser = subparsers.add_parser("status", parents=[parent], help="Show agent status")
    status_parser.add_argument("--watch", "-w", action="store_true", help="Watch mode")

    logs_parser = subparsers.add_parser("logs", parents=[parent], help="View agent logs")
    logs_parser.add_argument("agent_id", help="Agent ID")
    logs_parser.add_argument("--tail", "-t", type=int, default=50, help="Number of lines")

    args = parser.parse_args(argv)

    if args.verbose:
        configure_logging("DEBUG")
    else:
        configure_logging("INFO")

    if args.command == "init":
        data = {"project": args.name, "status": "initialized"}
        print(format_output(data, args.output))
    elif args.command == "deploy":
        data = {"manifest": args.manifest, "status": "deployed"}
        print(format_output(data, args.output))
    elif args.command == "status":
        data = {"agents": 3, "running": 2, "stopped": 1}
        print(format_output(data, args.output))
    elif args.command == "logs":
        data = [f"Agent {args.agent_id} log entry {i}" for i in range(1, 4)]
        print(format_output(data, args.output))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    cli()
