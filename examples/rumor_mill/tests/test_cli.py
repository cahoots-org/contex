from examples.rumor_mill.cli import Command, parse_command


def test_parse_command_forms():
    assert parse_command("Marta: hello there") == Command("whisper", "Marta", "hello there")
    assert parse_command("whisper Marta: hi") == Command("whisper", "Marta", "hi")
    assert parse_command("Old Bert: hello") == Command("whisper", "Old Bert", "hello")
    assert parse_command("villagers") == Command("villagers")
    assert parse_command("inspect Old Bert") == Command("inspect", "Old Bert")
    assert parse_command("goals") == Command("goals")
    assert parse_command("quit") == Command("quit")
    assert parse_command("  quit  ") == Command("quit")
    assert parse_command("dance") == Command("unknown")
    assert parse_command("") == Command("unknown")
    assert parse_command(": nobody") == Command("unknown")
