import pytest

from pypeline.energy_technology.tech_loader import _SPEC_INFERENCE, _load_specs_from_yaml
from pypeline.energy_technology.technology_stage import TechnologyStage, TechnologyCategory


@pytest.mark.parametrize(
    "name,expected_in,expected_out",
    [
        ("heat_grid_pipe_in", "district_heat_in", "district_heat_out"),
        ("district_heat_grid_branch", "district_heat_in", "district_heat_out"),
    ],
)
def grid_names_t(name, expected_in, expected_out):
    """Checks inferred heat-grid naming maps to default district commodities."""
    item = {"name": name}
    result = _SPEC_INFERENCE.apply(dict(item), set(item.keys()))
    assert result["category"] == TechnologyCategory.GRID.value
    assert result["stage"] == TechnologyStage.STAGE2.value
    assert result["commodity_in"] == expected_in
    assert result["commodity_out"] == expected_out


def grid_explicit_t():
    """Checks explicit commodity mappings are preserved during inference normalization."""
    item = {
        "name": "heat_grid_pipe_custom",
        "commodity_in": "pipe_inlet",
        "commodity_out": "pipe_outlet",
    }
    result = _SPEC_INFERENCE.apply(dict(item), set(item.keys()))
    assert result["commodity_in"] == "pipe_inlet"
    assert result["commodity_out"] == "pipe_outlet"
    assert result["category"] == TechnologyCategory.GRID.value
    assert result["stage"] == TechnologyStage.STAGE2.value


def invalid_yaml_t(tmp_path):
    """Checks invalid technology YAML entries raise descriptive validation errors."""
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text(
        "bad_tech:\n"
        "  commodity_out: heat\n"
    )

    with pytest.raises(ValueError) as excinfo:
        _load_specs_from_yaml(yaml_path, None)

    message = str(excinfo.value)
    assert "bad_tech" in message
    assert str(yaml_path) in message
