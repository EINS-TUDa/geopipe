
def year_dep_value_to_cesm_string(value: float | dict[int, float] | None) -> float | str:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        segments = []
        for year, value in sorted(value.items()):
            year_i = int(year)
            value_f = float(value)
            segments.append(f"{year_i} {value_f:.10g}")
        return "[" + " ; ".join(segments) + "]"
    raise TypeError(f"Unsupported type for year-dependent value: {type(value).__name__}")