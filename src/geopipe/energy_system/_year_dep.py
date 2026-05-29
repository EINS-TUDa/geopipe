# coding=utf-8
YearDep = float | dict[int, float] | None

def get_earliest_year(year_dep_dict: YearDep) -> int | None:
    if isinstance(year_dep_dict, dict):
        return min(year_dep_dict.keys())
    return None

def get_earliest_year_value(year_dep_dict: YearDep) -> float | None:
    if isinstance(year_dep_dict, dict):
        return year_dep_dict[get_earliest_year(year_dep_dict)]
    return year_dep_dict