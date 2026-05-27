"""Simple argument parser to replace argparse."""

import sys
from typing import Any, List

from plotly_cloud._definitions import CommandArgument


class ParsedArguments:
    """Simple namespace for parsed command arguments."""

    def __init__(self, **kwargs: Any) -> None:
        object.__setattr__(self, "_data", kwargs)

    def __getattribute__(self, name: str) -> Any:
        """Get attribute from internal data storage."""
        if name == "_data":
            return object.__getattribute__(self, name)
        data = object.__getattribute__(self, "_data")
        if name in data:
            return data[name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        """Set attribute in internal data storage."""
        if name == "_data":
            object.__setattr__(self, name, value)
        else:
            if not hasattr(self, "_data"):
                object.__setattr__(self, "_data", {})
            self._data[name] = value


def parse_group_and_command() -> tuple[str, str, int]:
    """Parse group and command from command line.

    Returns:
        Tuple of (group, command, start_index) where start_index is where to start parsing args
    """
    args = sys.argv[1:]

    # Parse group and command first (if present)
    if len(args) >= 2 and not args[0].startswith("-") and not args[1].startswith("-"):
        return args[0], args[1], 3  # Skip group and command for further parsing
    elif len(args) >= 1 and not args[0].startswith("-"):
        return args[0], "help", 2  # Default to help if only group provided
    else:
        return "help", "help", 1  # Default to help if no group provided


def parse_args(command_arguments: List[CommandArgument], args_index=3) -> ParsedArguments:
    """Parse command line arguments starting after group and command.

    Args:
        command_arguments: List of CommandArgument definitions

    Returns:
        ParsedArguments with all argument keys set
    """
    # Start parsing after group and command (sys.argv[3:])
    args = sys.argv[args_index:]
    result = {}

    # Separate positional and optional arguments
    positional_args = [arg for arg in command_arguments if not arg["name"].startswith("-")]
    optional_args = [arg for arg in command_arguments if arg["name"].startswith("-")]

    # Initialize all arguments with their defaults
    for arg_spec in command_arguments:
        name = arg_spec["name"]
        # Convert argument name to dictionary key (remove -- and convert - to _)
        if name.startswith("--"):
            key = name[2:].replace("-", "_")
        elif name.startswith("-"):
            key = name[1:].replace("-", "_")
        else:
            key = name.replace("-", "_")

        result[key] = arg_spec.get("default")

    # Add global verbose flag and help flag
    result["verbose"] = False
    result["help"] = False

    # Check if the first argument is "help" and handle it specially
    if len(args) > 0 and args[0] == "help":
        result["help"] = True
        args = args[1:]  # Skip the "help" argument

    # Track positional arguments
    positional_index = 0
    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ["--verbose", "-v"]:
            result["verbose"] = True
            i += 1
            continue

        if arg in ["--help", "-h"]:
            result["help"] = True
            i += 1
            continue

        # Check if this is an optional argument
        if arg.startswith("-"):
            # Find matching optional argument specification
            arg_spec = None
            for spec in optional_args:
                if spec["name"] == arg:
                    arg_spec = spec
                    break

            if not arg_spec:
                # Skip unknown arguments for now
                i += 1
                continue

            # Get the key name
            name = arg_spec["name"]
            if name.startswith("--"):
                key = name[2:].replace("-", "_")
            elif name.startswith("-"):
                key = name[1:].replace("-", "_")
            else:
                key = name.replace("-", "_")

            action = arg_spec.get("action", "store")

            if action == "store_true":
                result[key] = True
                i += 1
            elif action == "store":
                # Get the next argument as the value
                if i + 1 < len(args):
                    value = args[i + 1]
                    arg_type = arg_spec.get("type")
                    if arg_type and callable(arg_type):
                        try:
                            value = arg_type(value)
                        except (ValueError, TypeError):
                            pass  # Keep as string if conversion fails
                    result[key] = value
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        else:
            # This is a positional argument
            if positional_index < len(positional_args):
                arg_spec = positional_args[positional_index]
                name = arg_spec["name"]
                key = name.replace("-", "_")

                # Apply type conversion if specified
                value = arg
                arg_type = arg_spec.get("type")
                if arg_type and callable(arg_type):
                    try:
                        value = arg_type(value)
                    except (ValueError, TypeError):
                        pass  # Keep as string if conversion fails

                result[key] = value
                positional_index += 1

            i += 1

    return ParsedArguments(**result)
