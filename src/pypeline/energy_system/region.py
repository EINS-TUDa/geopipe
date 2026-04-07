from __future__ import annotations

from pypeline.data.data_registry import DataRegistry
from pypeline.data.dataset import CensusTechnology
from pypeline.validation import normalize_shares_or_zero, to_int_id
from pypeline.energy_system.demand import Demand, RegionDemand
from pypeline.energy_system.rule_book import HEAT_EXCHANGER_NAMES, PRIMARY_HEAT_EXCHANGER, RegionRuleBook
import geopandas as gpd

from pypeline.energy_technology.technology import (
    CENTRAL_TECH_PREFIX,
    INDIRECT_TECH_PREFIX,
    Technology,
    RegionTechnology,
)
from pypeline.energy_technology.technology_registry import (
    TechnologyRegistry,
    TechnologyNotFoundError,
)

REGIONAL_TECH_ALIAS_MAP: dict[str, tuple[str, ...]] = {PRIMARY_HEAT_EXCHANGER: HEAT_EXCHANGER_NAMES[1:]}
EXCLUDED_TECH_NAMES: tuple[str, ...] = ("heat_pipe",)


def _is_excluded(name: str) -> bool:
    return name in EXCLUDED_TECH_NAMES

def _indirect_base_names(registry: TechnologyRegistry) -> tuple[str, ...]:
    candidates: list[str] = []
    for name in registry.get_all(return_type="name"):
        if not name.startswith(INDIRECT_TECH_PREFIX):
            continue
        if "_D" in name:
            continue
        candidates.append(name)
    return _dedupe_preserve_order(candidates)


def _is_central_base(name: str) -> bool:
    return name.startswith(CENTRAL_TECH_PREFIX)


def _dedupe_preserve_order(names: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return tuple(ordered)


def _central_base_names(registry: TechnologyRegistry) -> tuple[str, ...]:
    candidates: list[str] = []
    for name in registry.get_all(return_type="name"):
        if "_D" in name:
            continue
        canonical = _canonical_regional_base(name)
        if _is_central_base(canonical) and registry.has_technology(canonical):
            candidates.append(canonical)
    return _dedupe_preserve_order(candidates)


def _regional_base_names(registry: TechnologyRegistry) -> tuple[str, ...]:
    base_names: list[str] = list(_central_base_names(registry))
    for static_name in ("heat_grid", PRIMARY_HEAT_EXCHANGER, *_indirect_base_names(registry)):
        if _is_excluded(static_name):
            continue
        if registry.has_technology(static_name):
            base_names.append(static_name)
    return _dedupe_preserve_order(base_names)


def _all_regional_base_tokens(registry: TechnologyRegistry) -> tuple[str, ...]:
    tokens: list[str] = list(_regional_base_names(registry))
    for aliases in REGIONAL_TECH_ALIAS_MAP.values():
        tokens.extend(aliases)
    return _dedupe_preserve_order(tokens)


def _split_unit_suffix(name: str) -> tuple[str, str | None]:
    if "_U" not in name:
        return name, None
    base, suffix = name.rsplit("_U", 1)
    if suffix.isdigit():
        return base, suffix
    return name, None


def _strip_unit_suffix(name: str) -> str:
    base, _ = _split_unit_suffix(name)
    return base


def _unit_suffix(idx: int) -> str:
    if idx <= 0:
        return ""
    return f"_U{idx}"


def _district_suffix(district_id: int) -> str:
    return f"_D{district_id}"


def _split_district_suffix(name: str) -> tuple[str, str | None]:
    if "_D" not in name:
        return name, None
    base, suffix = name.rsplit("_D", 1)
    if suffix.isdigit():
        return base, suffix
    return name, None


def _localized_name(base_name: str, district_id: int) -> str:
    return f"{base_name}{_district_suffix(district_id)}"


def _canonical_regional_base(name: str) -> str:
    for canonical, aliases in REGIONAL_TECH_ALIAS_MAP.items():
        if name == canonical or name in aliases:
            return canonical
    return name


def _canonical_regional_name(name: str) -> str:
    base_no_unit, unit_suffix = _split_unit_suffix(name)
    if "_D" not in base_no_unit:
        canonical_base = _canonical_regional_base(base_no_unit)
        result = canonical_base
    else:
        base, suffix = base_no_unit.rsplit("_D", 1)
        canonical_base = _canonical_regional_base(base)
        if canonical_base == base:
            result = base_no_unit
        else:
            result = f"{canonical_base}_D{suffix}"
    if unit_suffix is not None:
        return f"{result}_U{unit_suffix}"
    return result


def _is_regional_clone(name: str) -> bool:
    base_no_unit = _strip_unit_suffix(name)
    base, district_suffix = _split_district_suffix(base_no_unit)
    if district_suffix is None:
        return False
    canonical_base = _canonical_regional_base(base)
    if _is_central_base(canonical_base):
        return True
    return canonical_base in {"heat_grid", PRIMARY_HEAT_EXCHANGER}


def _is_other_district_clone(name: str, district_id: int) -> bool:
    canonical = _canonical_regional_name(name)
    base_no_unit = _strip_unit_suffix(canonical)
    base, district_suffix = _split_district_suffix(base_no_unit)
    if district_suffix is None:
        return False
    canonical_base = _canonical_regional_base(base)
    if not (_is_central_base(canonical_base) or canonical_base in {"heat_grid", PRIMARY_HEAT_EXCHANGER}):
        return False
    expected = _localized_name(canonical_base, district_id)
    return base_no_unit != expected


def _safe_int(raw, fallback: int = 0) -> int:
    try:
        return to_int_id(raw)
    except Exception:
        return fallback


class Region:
    def __init__(self,
                 id_: int,
                 polygon: gpd.GeoDataFrame,
                 region_demands: list["RegionDemand"],
                 region_technologies: list[RegionTechnology],
                 local_dhn_capex_base_eur: float | None = None):
        self.id = id_
        self.polygon = polygon
        self.region_demands = region_demands
        self.region_technologies = region_technologies
        self.local_dhn_capex_base_eur = local_dhn_capex_base_eur

    def get_demand(self, name: str) -> RegionDemand | None:
        for demand in self.region_demands:
            if demand.demand.demand_type == name:
                return demand
        return None


class RegionBuilder:
    def __init__(self,
                 technology_registry: TechnologyRegistry,
                 data_registry: DataRegistry,
                 base_crs: str = "EPSG:25832", ):
        self.base_crs = base_crs
        self.data_registry = data_registry
        self.technology_registry = technology_registry

        self.rule_book = None
        self.config = None
        self.demands = None
        self._dhn_central_seed_cache: dict[int, str] = {}

    def _infer_dhn_central_seed_base(self, polygon: gpd.GeoDataFrame, district_id: int) -> str:
        cached = self._dhn_central_seed_cache.get(int(district_id))
        if cached:
            return cached

        base_query = {
            "key": "heating_shares",
            "region": polygon,
            "base_crs": self.base_crs,
            "name_mapping": {},
        }
        raw_shares = self.data_registry.query(base_query)
        if not isinstance(raw_shares, dict):
            raise TypeError(f"Heating share query must return a mapping, got {type(raw_shares)} for district {district_id}")

        candidates: list[tuple[str, float]] = [
            ("cen_gas_boiler", float(raw_shares.get(CensusTechnology.Gas, 0.0) or 0.0)),
            ("cen_oil_boiler", float(raw_shares.get(CensusTechnology.Oil, 0.0) or 0.0)),
            (
                "cen_biomass_woodpellets",
                float(raw_shares.get(CensusTechnology.Wood, 0.0) or 0.0)
                + float(raw_shares.get(CensusTechnology.Biomass, 0.0) or 0.0),
            ),
        ]
        candidates.sort(key=lambda entry: (entry[1], entry[0]), reverse=True)

        viable = [
            (name, score)
            for name, score in candidates
            if score > 0.0 and self.technology_registry.has_technology(name)
        ]
        if not viable:
            raise ValueError(
                "Unable to infer district-heating central technology from local fuel shares "
                f"for district {district_id}. Expected positive Gas/Heizoel/Wood/Biomass shares."
            )

        selected = viable[0][0]

        self._dhn_central_seed_cache[int(district_id)] = selected
        return selected

    def set_demands(self, demands: list[Demand]):
        if not isinstance(demands, list) or not all(isinstance(d, Demand) for d in demands):
            raise TypeError(f"demands must be a list of Demand instances and not {type(demands)}.")
        self.demands = demands
        return self

    def set_rule_book(self, rule_book: RegionRuleBook):
        if not isinstance(rule_book, RegionRuleBook):
            raise TypeError(f"rule_book must be an instance of RegionRuleBook and not {type(rule_book)}.")
        self.rule_book = rule_book
        return self

    def set_config(self, config: dict):
        self.config = config
        return self


    def build_demands(self, polygon) -> list[RegionDemand]:
        collection = []
        for demand in self.demands:
            base_query = {"region": polygon, "base_crs": self.base_crs}

            profile = self.data_registry.query(demand.profile_query_params | base_query)
            if profile is None or profile.empty:
                raise ValueError(f"Demand profile for {demand.demand_type} not found in data registry.")

            demand_value = self.data_registry.query(demand.demand_query_params | base_query)
            if demand_value is None or not isinstance(demand_value, (int, float)):
                raise ValueError(f"Demand value for {demand.demand_type} not found or invalid in data registry.")

            # Create a RegionDemand instance
            region_demand = RegionDemand(
                demand=demand,
                value=demand_value,
                profile=profile
            )
            collection.append(region_demand)
        return collection

    def _ensure_regional_assets(self, district_id: int) -> None:
        suffix = _district_suffix(district_id)
        registry = self.technology_registry
        regional_base_names = _regional_base_names(registry)
        regional_base_set = set(regional_base_names)

        # Create localized clones for regional technologies
        localized: dict[str, str] = {
            base: _localized_name(base, district_id) for base in regional_base_names
        }

        for base_name, localized_base in localized.items():
            if not registry.has_technology(base_name):
                continue
            base_tech = registry.get_by_name(base_name)
            max_units_attr = base_tech.max_units
            unlimited = False
            units = 1
            if max_units_attr is None:
                unlimited = True
            else:
                try:
                    units = max(1, int(max_units_attr))
                except Exception:
                    unlimited = True
                    units = 1

            existing_names = self._clone_names(localized_base)

            for extra_name in existing_names[1:]:
                try:
                    registry.remove(extra_name)
                except TechnologyNotFoundError:
                    continue
            for alias in REGIONAL_TECH_ALIAS_MAP.get(base_name, ()):
                alias_base = _localized_name(alias, district_id)
                alias_names = self._clone_names(alias_base)
                for extra_name in alias_names[1:]:
                    try:
                        registry.remove(extra_name)
                    except TechnologyNotFoundError:
                        continue

            if unlimited:
                self._ensure_clone(
                    base_name=base_name,
                    base_tech=base_tech,
                    district_id=district_id,
                    unit_idx=0,
                    suffix=suffix,
                    unlimited=True,
                    total_units=None,
                )
                continue

            self._ensure_clone(
                base_name=base_name,
                base_tech=base_tech,
                district_id=district_id,
                unit_idx=0,
                suffix=suffix,
                unlimited=False,
                total_units=units,
            )

    def _clone_names(self, localized_base: str) -> list[str]:
        registry = self.technology_registry
        names: list[str] = []
        idx = 0
        while True:
            name = f"{localized_base}{_unit_suffix(idx)}"
            if not registry.has_technology(name):
                break
            names.append(name)
            idx += 1
        return names

    def _ensure_clone(
        self,
        base_name: str,
        base_tech: Technology,
        district_id: int,
        unit_idx: int,
        suffix: str,
        unlimited: bool,
        total_units: int | None,
    ) -> None:
        registry = self.technology_registry
        localized_base = _localized_name(base_name, district_id)
        suffix_unit = _unit_suffix(unit_idx)
        localized_name = f"{localized_base}{suffix_unit}"

        commodity_in = base_tech.commodity_in
        commodity_out = base_tech.commodity_out

        if base_name == "heat_grid":
            commodity_in = f"district_heat_in{suffix}"
            commodity_out = f"district_heat_out{suffix}"
        elif _is_central_base(base_name):
            commodity_out = f"district_heat_in{suffix}"
        elif base_name == PRIMARY_HEAT_EXCHANGER:
            commodity_in = f"district_heat_out{suffix}"

        cap_max_value = None if unlimited else base_tech.cap_max
        max_units_value = None if unlimited else total_units
        cap_min_value = base_tech.cap_min
        availability = base_tech.availability_profile
        capex_base = base_tech.capex_cost_base

        if registry.has_technology(localized_name):
            clone = registry.get_by_name(localized_name)
            clone.commodity_in = commodity_in
            clone.commodity_out = commodity_out
            clone.efficiency = base_tech.efficiency
            clone.technical_lifetime = base_tech.technical_lifetime
            clone.opex_cost_energy = base_tech.opex_cost_energy
            clone.opex_cost_power = base_tech.opex_cost_power
            clone.capex_cost_power = base_tech.capex_cost_power
            clone.capex_cost_base = capex_base
            clone.cap_min = cap_min_value
            clone.cap_max = cap_max_value
            clone.max_units = max_units_value
            clone.availability_profile = availability
            clone.stage = base_tech.stage
            clone.category = base_tech.category
        else:
            clone = Technology(
                name=localized_name,
                commodity_in=commodity_in,
                commodity_out=commodity_out,
                efficiency=base_tech.efficiency,
                technical_lifetime=base_tech.technical_lifetime,
                opex_cost_energy=base_tech.opex_cost_energy,
                opex_cost_power=base_tech.opex_cost_power,
                capex_cost_power=base_tech.capex_cost_power,
                capex_cost_base=capex_base,
                cap_min=cap_min_value,
                cap_max=cap_max_value,
                max_units=max_units_value,
                availability_profile=availability,
                stage=base_tech.stage,
                category=base_tech.category,
            )
            registry.register(clone)

        for alias in REGIONAL_TECH_ALIAS_MAP.get(base_name, ()):  # legacy aliases for compatibility
            alias_base = _localized_name(alias, district_id)
            alias_name = f"{alias_base}{suffix_unit}"
            if registry.has_technology(alias_name):
                alias_clone = registry.get_by_name(alias_name)
                alias_clone.commodity_in = clone.commodity_in
                alias_clone.commodity_out = clone.commodity_out
                alias_clone.efficiency = clone.efficiency
                alias_clone.technical_lifetime = clone.technical_lifetime
                alias_clone.opex_cost_energy = clone.opex_cost_energy
                alias_clone.opex_cost_power = clone.opex_cost_power
                alias_clone.capex_cost_power = clone.capex_cost_power
                alias_clone.capex_cost_base = clone.capex_cost_base
                alias_clone.cap_min = clone.cap_min
                alias_clone.cap_max = clone.cap_max
                alias_clone.max_units = clone.max_units
                alias_clone.availability_profile = clone.availability_profile
                alias_clone.stage = clone.stage
                alias_clone.category = clone.category
            else:
                alias_clone = Technology(
                    name=alias_name,
                    commodity_in=clone.commodity_in,
                    commodity_out=clone.commodity_out,
                    efficiency=clone.efficiency,
                    technical_lifetime=clone.technical_lifetime,
                    opex_cost_energy=clone.opex_cost_energy,
                    opex_cost_power=clone.opex_cost_power,
                    capex_cost_power=clone.capex_cost_power,
                    capex_cost_base=clone.capex_cost_base,
                    cap_min=clone.cap_min,
                    cap_max=clone.cap_max,
                    max_units=clone.max_units,
                    availability_profile=clone.availability_profile,
                    stage=clone.stage,
                    category=clone.category,
                )
                registry.register(alias_clone)

    def build_technologies(self, polygon: gpd.GeoDataFrame, r_demands: list[RegionDemand], district_id: int) -> list[RegionTechnology]:
        collection: list[RegionTechnology] = []
        technologies_with_shares: set[str] = set()
        central_seed_outputs: dict[str, float] = {}
        central_seed_profile_peaks: dict[str, float] = {}
        suffix = _district_suffix(district_id)
        registry = self.technology_registry
        raw_names = registry.get_all(return_type="name")
        all_regional_tokens = set(_all_regional_base_tokens(registry))
        regional_base_names = _regional_base_names(registry)
        regional_base_set = set(regional_base_names)
        all_technologies: list[str] = []
        seen_names: set[str] = set()
        for name in raw_names:
            if _is_excluded(name):
                continue
            if name in all_regional_tokens:
                continue
            if _is_other_district_clone(name, district_id):
                continue
            canonical_name = _canonical_regional_name(name)
            if _is_excluded(canonical_name):
                continue
            lookup_name = canonical_name if registry.has_technology(canonical_name) else name
            if lookup_name in seen_names:
                continue
            seen_names.add(lookup_name)
            all_technologies.append(lookup_name)
        localized_map = {base: _localized_name(base, district_id) for base in regional_base_names}
        # Technologies which supply demands
        for r_demand in r_demands:
            if r_demand.demand.technology_shares_query_params is None:
                continue
            # if r_demand.demand.technology_shares_query_params:
            technologies_supplying = registry.get_by_output_commodity(
                commodity=r_demand.demand.commodity_in,
                return_type="name",
            )
            localized_suppliers = []
            for tech_name in technologies_supplying:
                canonical_name = _canonical_regional_name(tech_name)
                if _is_other_district_clone(canonical_name, district_id):
                    continue
                base_name = canonical_name.rsplit("_D", 1)[0] if "_D" in canonical_name else canonical_name
                if base_name in regional_base_set and canonical_name == base_name:
                    localized_suppliers.append(localized_map[base_name])
                else:
                    localized_suppliers.append(canonical_name)
            technologies_supplying_this_demand = localized_suppliers

            base_query = {"region": polygon, "base_crs": self.base_crs}
            technology_shares_data = self.data_registry.query(
                r_demand.demand.technology_shares_query_params | base_query)

            model_tech_shares = {}
            for tech_name, share in technology_shares_data.items():
                if not tech_name:
                    continue
                canonical_name = _canonical_regional_name(tech_name)
                if _is_other_district_clone(canonical_name, district_id):
                    continue
                base_name = canonical_name.rsplit("_D", 1)[0] if "_D" in canonical_name else canonical_name
                if base_name in regional_base_set and canonical_name == base_name:
                    mapped = localized_map[base_name]
                else:
                    mapped = canonical_name
                if mapped in technologies_supplying_this_demand:
                    model_tech_shares[mapped] = share

            normalized_shares = normalize_shares_or_zero(model_tech_shares)

            if not any(share > 0.0 for share in normalized_shares.values()):
                normalized_shares = {tech: 0.0 for tech in model_tech_shares}
                if r_demand.demand.default_supply_technology:
                    default_tech = r_demand.demand.default_supply_technology
                    canonical_default = _canonical_regional_base(default_tech)
                    if canonical_default in regional_base_set:
                        default_tech = localized_map[canonical_default]
                    if default_tech not in normalized_shares.keys():
                        raise ValueError(
                            f"Default supply technology {r_demand.demand.default_supply_technology} not found in "
                            f"the technology shares for demand {r_demand.demand.demand_type}."
                        )
                    normalized_shares[default_tech] = 1.0

            for tech, share in normalized_shares.items():
                initial_energy_output = r_demand.value * share
                region_technology = RegionTechnology(
                    technology=self.technology_registry.get_by_name(tech),
                    initial_energy_output=initial_energy_output,
                    initial_capacity= max(r_demand.profile)*initial_energy_output * self.config["cap_factor_ind_technologies"],
                    output_profile=r_demand.profile,
                )
                collection.append(region_technology)
                technologies_with_shares.add(tech)

                localized_heat_exchanger = localized_map.get(PRIMARY_HEAT_EXCHANGER)
                if (
                    localized_heat_exchanger
                    and tech == localized_heat_exchanger
                    and initial_energy_output > 0.0
                ):
                    central_seed_base = self._infer_dhn_central_seed_base(polygon, district_id)
                    localized_central_default = localized_map.get(central_seed_base)
                    if not localized_central_default:
                        raise ValueError(
                            f"Missing localized central technology '{central_seed_base}' for district {district_id}"
                        )
                    if not self.technology_registry.has_technology(localized_central_default):
                        raise ValueError(
                            f"Technology registry missing '{localized_central_default}' for district {district_id}"
                        )
                    central_seed_outputs[localized_central_default] = (
                        central_seed_outputs.get(localized_central_default, 0.0) + float(initial_energy_output)
                    )
                    central_seed_profile_peaks[localized_central_default] = max(
                        central_seed_profile_peaks.get(localized_central_default, 0.0),
                        float(max(r_demand.profile)),
                    )

        for central_name, seeded_output in central_seed_outputs.items():
            profile_peak = central_seed_profile_peaks.get(central_name, 0.0)
            seeded_capacity = profile_peak * seeded_output * self.config["cap_factor_ind_technologies"]
            existing = next((item for item in collection if item.technology.name == central_name), None)
            if existing is not None:
                existing.initial_energy_output += seeded_output
                existing.initial_capacity += seeded_capacity
            else:
                collection.append(
                    RegionTechnology(
                        technology=self.technology_registry.get_by_name(central_name),
                        initial_energy_output=seeded_output,
                        initial_capacity=seeded_capacity,
                        output_profile=None,
                    )
                )
            technologies_with_shares.add(central_name)

       
        other_technologies = set(all_technologies) - technologies_with_shares
        for tech_name in other_technologies:
            tech = self.technology_registry.get_by_name(tech_name)
            initial_output = 0.0
            initial_capacity = 0.0
            profile = None
            region_technology = RegionTechnology(
                technology=tech,
                initial_energy_output=initial_output,
                initial_capacity=initial_capacity,
                output_profile=profile
            )
            collection.append(region_technology)

        return collection

    def build(self, polygon) -> Region:
        district_id = _safe_int(polygon.get("id"), 0)
        demands = self.build_demands(polygon)
        self._ensure_regional_assets(district_id)
        technologies = self.build_technologies(polygon, demands, district_id)

        region = Region(
            id_=district_id,
            polygon=polygon,
            region_technologies=technologies,
            region_demands=demands
        )

        if self.rule_book:
            region = self.rule_book.apply(region)

        return region
