from __future__ import annotations
from enum import Enum


class TechnologyStage(str, Enum):
    """Lifecycle/connection stage of a technology within the heating chain.

    Stage1: Indirect / decentralized technologies directly connected to demands
            and to a stage-2 node (grid or dummy via a heat exchanger concept).
    Stage2: Network layer (heat grid / hydrogen grid) or dummy aggregation points;
            can connect to Stage1 and Stage3.
    Stage3: Centralized supply / large-scale assets feeding into Stage2 only.
    """

    STAGE1 = "stage1"
    STAGE2 = "stage2"
    STAGE3 = "stage3"


class TechnologyCategory(str, Enum):
    """Categorization orthogonal to stage for optional filtering.

    - DEMAND_LINK: Technologies sitting directly at or immediately upstream of demand.
    - GRID: Network / transport mediums (heat grid, hydrogen grid, etc.).
    - SUPPLY: Central generation / production assets.
    - DUMMY: Placeholder / aggregation / boundary nodes.
    """

    DEMAND_LINK = "demand_link"
    GRID = "grid"
    SUPPLY = "supply"
    DUMMY = "dummy"


DEFAULT_STAGE = TechnologyStage.STAGE1
DEFAULT_CATEGORY = TechnologyCategory.DEMAND_LINK
