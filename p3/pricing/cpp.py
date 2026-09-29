"""XJet CostPerPart (CPP) manufacturing-cost chain, "Single" stacking mode.

Independent port of Pipeline 2's app/PipelineBase.py:_RunCPPCalc (revision
1e871734), cost part only — feasibility heuristics are dropped because Pipeline 3
has no measured geometry. Data comes from cpp_db.json (a snapshot of P2's
vendored constants.js + cpp-overrides.js + metal-map.js, see its _provenance).

IMPORTANT: this chain needs bounding-box dimensions as well as volume (tray
packing and print time depend on them). Pipeline 3 fixes volume at 1 cm³ but
the dimensions must come from an explicitly configured pricing profile. This
module never guesses them.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

_DbPath = Path(__file__).with_name("cpp_db.json")

DefaultShopParameters = {
    "electricity_usd_per_kwh": 0.10, "labor_usd_per_hr": 35.0, "overhead_factor": 0.15,
    "machine_cost_usd": 728000.0, "smart_price_usd": 0.0,
    "dep_years_machine": 5.0, "dep_years_furnace": 7.0, "service_rate": 0.05,
}


@lru_cache(maxsize=1)
def LoadCppDb() -> dict:
    return json.loads(_DbPath.read_text(encoding="utf-8"))


def CppDbVersion() -> str:
    from p3.config import ContentVersion
    return ContentVersion("cpp", LoadCppDb())


def CalculateCost(CppMetal: str, XMm: float, YMm: float, ZMm: float, VolumeCm3: float,
                  ShopParameters: dict | None = None) -> dict:
    """Return {"cost_usd", "margin", "price_usd", "weight_g"} or raise ValueError."""
    DB = LoadCppDb()
    Mapped = DB["metal_map"].get(CppMetal)
    if not Mapped or Mapped["matKey"] not in DB["materials"]:
        raise ValueError(f"No CPP material mapping for {CppMetal!r}")
    if not all(V and V > 0 for V in (XMm, YMm, ZMm, VolumeCm3)):
        raise ValueError("Dimensions and volume must be positive")
    P = {**DefaultShopParameters, **(ShopParameters or {})}

    MatKey = Mapped["matKey"]
    Mat    = dict(DB["materials"][MatKey])
    Mat["density"] = Mapped["density"]
    Mat["price"]   = DB["materials"][MatKey]["price"] * Mapped["priceScale"]
    Sup    = DB["support"][MatKey];   Tray   = DB["tray"][MatKey]["Single"]
    Clean  = DB["cleaning"][MatKey];  Sinter = DB["sintering"][MatKey]
    Debind = DB["debinding"][MatKey]; Labor  = DB["labor"][MatKey]
    Dep    = DB["depreciation"]
    ElecPrice = P["electricity_usd_per_kwh"]; LaborRate = P["labor_usd_per_hr"]
    OhFactor  = P["overhead_factor"];         ServiceRate = P["service_rate"]
    DepYrsMach = P["dep_years_machine"];      DepYrsFurn = P["dep_years_furnace"]
    TotalEquip = P["machine_cost_usd"] + P["smart_price_usd"]

    GreenX = math.ceil(XMm / (1 - Mat["shrinkage"]) * 100) / 100
    GreenY = math.ceil(YMm / (1 - Mat["shrinkage"]) * 100) / 100
    GreenZ = math.ceil(ZMm / (1 - Mat["shrinkage"]) * 100) / 100
    Env = DB["margins"]["envelope"]
    # P2's Python port hard-coded 500 x 131.98; the browser used the tray row. Use the tray row.
    C28 = max(1, math.floor(Tray["maxX"] / (GreenX + Env * 2)) * math.floor(Tray["maxY"] / (GreenY + Env * 2)))
    C32 = C28; C33 = C32 * Dep["machinesPerSintering"]
    PartWeight  = VolumeCm3 * Mat["density"]
    NumLayers   = (GreenZ + DB["margins"]["pedestal"]) * 1000 / Mat["layerThickness"]
    PrintTimeHr = NumLayers * Tray["layerTime"] / 3600
    C38 = PartWeight * C32; C39 = C38 / Mat["powderInk"]
    DefRate = DB.get("defectRateOverride", 0) or 0
    C41 = (C39 / Mat["densityPct"] * (1 + DefRate)) * Sup["consumptionWeight"] * (1 + DefRate)
    C40 = C39 / Mat["densityPct"] * (1 + DefRate) + C41 * Sup["buildUsed"] + C39 * DB["rollerFactor"]
    C43 = C40 * Mat["price"] / 1000 + C41 * Sup["price"] / 1000; C44 = C43 / C32
    MachDepHr = TotalEquip / (DepYrsMach * Dep["machDays"] * Dep["machHours"])
    MachSvcHr = TotalEquip * ServiceRate / (Dep["machDays"] * Dep["machHours"])
    C56 = PrintTimeHr * ElecPrice * DB["machinePowerKW"]; C57 = PrintTimeHr * MachDepHr
    C58 = PrintTimeHr * MachSvcHr
    C66 = Labor["trayPrep"] + PrintTimeHr * Labor["machine"]; C68 = C66 * LaborRate / C32
    C77 = Clean["workTime"] * Clean["power"] * ElecPrice; C78 = Clean["waterCons"] * Clean["waterCost"]
    C87 = Labor["supportRemoval"] * LaborRate
    Yld = Mat["yield"]; C93 = Debind["time"] * Yld
    DebDepHr = Dep["debindPrice"] / (DepYrsFurn * Dep["sinterDays"] * Dep["sinterHours"])
    DebSvcHr = Dep["debindPrice"] * ServiceRate / (Dep["sinterDays"] * Dep["sinterHours"])
    C95 = C93 * Debind["power"] * ElecPrice; C96 = C93 * DebDepHr; C97 = C93 * DebSvcHr
    C102 = Labor["debinding"] * Yld; C119 = Labor["sintering"]; C120 = C119 * LaborRate; C103 = C102 * C120
    C109 = Sinter["time"]; C111 = C109 * Sinter["power"] * ElecPrice; C112 = Sinter["gasCons"] * Sinter["gasCost"]
    SintDepHr = Dep["sinterCeramicPrice"] / (DepYrsFurn * Dep["sinterDays"] * Dep["sinterHours"])
    SintSvcHr = Dep["sinterCeramicPrice"] * ServiceRate / (Dep["sinterDays"] * Dep["sinterHours"])
    C114 = C109 * SintDepHr; C115 = C109 * SintSvcHr
    PwMat = C44; PwUtil = C56 / C32 + C77 / C32 + C78 / C32; PwLabor = C68 + C87 / C32
    PwSvc = C58 / C32; PwDep = C57 / C32
    SdUtil = C95 / C32 + (C111 + C112) / C33; SdLabor = C103 / C32 + C120 / C33
    SdSvc = C97 / C32 + C115 / C33; SdDep = C96 / C32 + C114 / C33
    PwVarSub = PwMat + PwUtil + PwLabor + PwSvc; SdVarSub = SdUtil + SdLabor + SdSvc
    TotalPP = (PwVarSub + PwDep + (PwDep + PwVarSub) * OhFactor
               + SdVarSub + SdDep + (SdDep + SdVarSub) * OhFactor)

    Margin = Mapped.get("margin", 0) or 0
    if not 0 <= Margin < 1:
        raise ValueError(f"Invalid margin for {CppMetal!r}: {Margin}")
    return {"cost_usd": TotalPP, "margin": Margin, "price_usd": TotalPP / (1 - Margin),
            "weight_g": PartWeight}
