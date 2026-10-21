from pathlib import Path

import pytest

import main


@pytest.mark.parametrize("args", [[], ["--storey", "Level 1"], ["--list-storeys"]])
def test_ifc_argument_is_required(args, capsys):
    with pytest.raises(SystemExit) as exc:
        main.parse_args(args)
    assert exc.value.code == 2
    assert "the following arguments are required: --ifc" in capsys.readouterr().err


def test_explicit_ifc_path_is_preserved():
    args = main.parse_args(["--ifc", "chosen.ifc", "--storey", "Level 1"])
    assert args.ifc == Path("chosen.ifc")
    assert args.storey == "Level 1"
