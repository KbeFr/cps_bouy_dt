"""Main CLI entry point for Plotly Cloud."""

import asyncio
import logging
import sys
import textwrap
from collections.abc import Sequence
from typing import Union

import nest_asyncio
from rich.console import Console
from rich.panel import Panel

from plotly_cloud._commands import BaseCommand, CommandRegistry
from plotly_cloud._definitions import CommandArgument, HelpPanelStyle
from plotly_cloud._parser import parse_args, parse_group_and_command
from plotly_cloud.exceptions import PlotlyCloudError

# Suppress asyncio task exception warnings during Flask reloads
# These occur when Flask's werkzeug reloader terminates the process
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

console = Console()


def create_help_panel(arguments: Sequence[Union[CommandArgument, dict]], title: str, style: HelpPanelStyle) -> None:
    """Create a Rich panel for command arguments with proper styling and layout."""
    formatted_lines = []

    for arg in arguments:
        # Format the argument name with padding and color
        arg_name = arg["name"].ljust(style["argument_width"])
        formatted_arg = f"[{style['argument_color']}]{arg_name}[/{style['argument_color']}]"

        # Format description with color and wrap to terminal width
        max_width = console.width - style["argument_width"] - 4  # Account for padding and spacing
        wrapper = textwrap.TextWrapper(width=max_width)
        wrapped_desc = wrapper.fill(arg["help"])
        description = f"[{style['description_color']}]{wrapped_desc}[/{style['description_color']}]"

        # Combine argument and description on same line
        arg_line = f"{formatted_arg} {description}"
        formatted_lines.append(arg_line)

        arg_default = arg.get("default")

        # Add default value on separate line if present
        if arg_default is not None:
            default_text = (
                f"[{style['description_color']}]{''.ljust(style['argument_width'])} "
                f"(default: {arg_default})[/{style['description_color']}]"
            )
            formatted_lines.append(default_text)

    content = "\n".join(formatted_lines)
    panel = Panel(
        content,
        title=title,
        title_align="left",
        border_style=style["border_style"],
        padding=(0, 1),
    )
    console.print(panel)


def create_error_panel(content, title="✗ Error"):
    console.print()
    panel = Panel(
        content,
        title=title,
        title_align="left",
        border_style="red",
        padding=(0, 1),
    )
    console.print(panel)


def print_main_help(show_banner=True) -> None:
    """Print main help with ASCII art and command groups."""
    # Add ASCII art for main command
    ascii_art = """[blue]
   ██████╗ ██╗      ██████╗ ████████╗██╗  ██╗   ██╗
   ██╔══██╗██║     ██╔═══██╗╚══██╔══╝██║  ╚██╗ ██╔╝
   ██████╔╝██║     ██║   ██║   ██║   ██║   ╚████╔╝
   ██╔═══╝ ██║     ██║   ██║   ██║   ██║    ╚██╔╝
   ██║     ███████╗╚██████╔╝   ██║   ███████╗██║
   ╚═╝     ╚══════╝ ╚═════╝    ╚═╝   ╚══════╝╚═╝[/blue]
[cyan]         📊 Interactive Data Visualization Platform 📈[/cyan]

[dim]Publish and manage your Dash applications with ease[/dim]

"""
    if show_banner:
        console.print(ascii_art)

    # Show command groups
    for group_name, group in CommandRegistry.commands.items():
        commands = []
        for cmd_name, cmd_class in group["commands"].items():
            commands.append({"name": f"{group_name} {cmd_name}", "help": cmd_class.short_description})

        commands_style: HelpPanelStyle = {
            "border_style": "dim blue",
            "argument_color": "yellow",
            "description_color": "dim",
            "argument_width": 32,
        }

        panel_title = f"{group_name.upper()} COMMANDS"
        create_help_panel(commands, panel_title, commands_style)

    # Show global options
    global_options = [
        {
            "name": "--verbose, -v",
            "help": "Enable verbose output with detailed error information",
        },
        {"name": "--help, -h", "help": "Show this help message and exit"},
    ]

    options_style: HelpPanelStyle = {
        "border_style": "dim white",
        "argument_color": "yellow",
        "description_color": "dim",
        "argument_width": 32,
    }

    create_help_panel(global_options, "GLOBAL OPTIONS", options_style)


def print_group_help(group_name: str) -> None:
    """Print help for a specific group."""
    group = CommandRegistry.commands.get(group_name)
    if not group:
        create_error_panel(
            f"Unknown command group: [yellow]'{group_name}[/yellow]'\n\n"
            f"Available groups: [cyan]{', '.join(CommandRegistry.commands.keys())}[/cyan]"
        )
        sys.exit(1)

    # Type guard to ensure group is not None
    assert group is not None

    # Create commands list for the panel
    commands = []
    for cmd_name, cmd_class in group["commands"].items():
        commands.append({"name": f"{group_name} {cmd_name}", "help": cmd_class.short_description})

    # Define style for group help
    group_style: HelpPanelStyle = {
        "border_style": "dim blue",
        "argument_color": "yellow",
        "description_color": "dim",
        "argument_width": 32,
    }

    panel_title = f"{group_name.upper()} COMMANDS"
    create_help_panel(commands, panel_title, group_style)

    console.print(f"\nUse 'plotly {group_name} <command> --help' for more information about a command.")


def print_command_help(command_class: BaseCommand, group: str, command: str) -> None:
    """Print help for a specific command."""
    console.print(f"\n[bold blue]plotly {group} {command}[/bold blue]\n")

    if command_class.description:
        console.print(f"{command_class.description}\n")

    # Use the PlotlyHelpFormatter logic for command arguments
    if command_class.arguments:
        # Define styles
        args_style: HelpPanelStyle = {
            "border_style": "dim blue",
            "argument_color": "yellow",
            "description_color": "dim",
            "argument_width": 32,
        }

        options_style: HelpPanelStyle = {
            "border_style": "dim white",
            "argument_color": "yellow",
            "description_color": "dim",
            "argument_width": 32,
        }

        # Separate positional and optional arguments
        positional_args = [arg for arg in command_class.arguments if not arg["name"].startswith("-")]
        optional_args = [arg for arg in command_class.arguments if arg["name"].startswith("-")]

        # Create panels for arguments
        if positional_args:
            create_help_panel(positional_args, "ARGUMENTS", args_style)

        if optional_args:
            create_help_panel(optional_args, "OPTIONS", options_style)

    # Show global options
    global_options = [
        {
            "name": "--verbose, -v",
            "help": "Enable verbose output with detailed error information",
        },
        {"name": "--help, -h", "help": "Show this help message and exit"},
    ]

    options_style: HelpPanelStyle = {
        "border_style": "dim white",
        "argument_color": "yellow",
        "description_color": "dim",
        "argument_width": 32,
    }

    create_help_panel(global_options, "GLOBAL OPTIONS", options_style)


def main() -> None:
    """Main CLI entry point."""
    # Allow to run multiple calls to asyncio run.
    nest_asyncio.apply()

    # Parse group and command
    group, command, args_index = parse_group_and_command()

    # Handle help command
    if group == "help" or command == "help":
        if group == "help" and command == "help":
            # Show main help
            print_main_help()
            sys.exit(0)
        elif command == "help":
            # Show group help
            print_group_help(group)
            sys.exit(0)

    try:
        # Find the command group
        group_data = CommandRegistry.commands.get(group)

        # Check if group exists
        if group_data is None:
            create_error_panel(
                f"Unknown command group: [yellow]'{group}'[/yellow]\n\n"
                f"Available groups: [cyan]{', '.join(CommandRegistry.commands.keys())}[/cyan]\n\n"
                f"Use [cyan]'plotly --help'[/cyan] to see all available commands."
            )
            sys.exit(1)

        # Type guard to ensure group_data is not None
        assert group_data is not None

        # Find the command in the group
        command_class = group_data["commands"].get(command)
        if not command_class:
            create_error_panel(
                f"Unknown command: {group} {command}\n\n"
                f"Available commands in [yellow]'{group}'[/yellow]: "
                f"[cyan]{', '.join(group_data['commands'].keys())}[/cyan]"
            )
            sys.exit(1)

        # Type guard to ensure command_class is not None
        assert command_class is not None

        # Parse arguments specific to this command
        command_args = parse_args(command_class.arguments)

        # Handle command help
        if command_args.help:
            print_command_help(command_class, group, command)
            sys.exit(0)

        # Execute the command
        asyncio.run(command_class.execute(command_args))

    except KeyboardInterrupt:
        sys.exit(1)
    except PlotlyCloudError as e:
        # Display custom exceptions in a red panel with class name as title
        args = parse_args([], args_index)
        if args.verbose:
            console.print_exception()
        else:
            content = str(e)
            if e.details:
                content += f"\n\n{e.details}"
            create_error_panel(content, f"✗ {e.__class__.__name__}")
        sys.exit(1)
    except Exception as e:
        # Fallback for unexpected exceptions
        args = parse_args([], args_index)
        if args.verbose:
            console.print_exception()
        else:
            create_error_panel(str(e), "✗ Unexpected Error")
        sys.exit(1)


if __name__ == "__main__":
    main()
