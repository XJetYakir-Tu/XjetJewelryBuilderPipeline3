"""Repository-owned generation config and material catalog.

Each generation section gets a config_version derived from its content, so
cached movies/meshes are only reused when the settings that produced them are
unchanged (spec section 9: include config versions in cache keys).
"""

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from p3.settings import ConfigDir


def ContentVersion(Prefix: str, Data) -> str:
    Digest = hashlib.sha256(json.dumps(Data, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{Prefix}-{Digest}"


def _ReadText(RelPath: str) -> str:
    return (ConfigDir / RelPath).read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class ImageConfig:
    CandidatesPerBatch: int
    MaxConcurrentRequests: int
    RequestTimeoutS: float
    MaxDuplicateRetriesPerSlot: int
    Params: dict
    GenerateSystemPrompt: str
    EditSystemPrompt: str
    PromptSuffix: str
    Version: str


@dataclass(frozen=True)
class JobConfig:
    RequestTimeoutS: float
    Params: dict
    Version: str


@dataclass(frozen=True)
class GenerationConfig:
    Images: ImageConfig
    Movie: JobConfig
    Mesh: JobConfig


def LoadGenerationConfig(ConfigPath: Path | None = None) -> GenerationConfig:
    Raw = json.loads((ConfigPath or ConfigDir / "generation.json").read_text(encoding="utf-8"))
    Img = Raw["images"]
    GenPrompt  = _ReadText(Img["generate_system_prompt_file"])
    EditPrompt = _ReadText(Img["edit_system_prompt_file"])
    Suffix     = _ReadText(Img["prompt_suffix_file"])
    if int(Img["candidates_per_batch"]) != 6:
        # Confirmed requirement: one prompt produces six candidates.
        raise ValueError("images.candidates_per_batch must be 6 (confirmed product requirement)")
    Images = ImageConfig(
        CandidatesPerBatch=6,
        MaxConcurrentRequests=max(1, int(Img["max_concurrent_requests"])),
        RequestTimeoutS=float(Img["request_timeout_s"]),
        MaxDuplicateRetriesPerSlot=max(0, int(Img["max_duplicate_retries_per_slot"])),
        Params=dict(Img["params"]),
        GenerateSystemPrompt=GenPrompt,
        EditSystemPrompt=EditPrompt,
        PromptSuffix=Suffix,
        Version=ContentVersion("img", {"params": Img["params"], "gen": GenPrompt,
                                       "edit": EditPrompt, "suffix": Suffix}),
    )
    Movie = JobConfig(float(Raw["movie"]["request_timeout_s"]), dict(Raw["movie"]["params"]),
                      ContentVersion("mov", Raw["movie"]["params"]))
    Mesh = JobConfig(float(Raw["mesh"]["request_timeout_s"]), dict(Raw["mesh"]["params"]),
                     ContentVersion("mesh", Raw["mesh"]["params"]))
    return GenerationConfig(Images=Images, Movie=Movie, Mesh=Mesh)


# ── Material catalog ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Material:
    Id: str
    Group: str
    Label: str
    CppMetal: str
    DensityGCm3: float
    RequiresPlating: bool
    Swatch: str
    Tint: str

    def ToJson(self) -> dict:
        return {"id": self.Id, "group": self.Group, "label": self.Label,
                "swatch": self.Swatch, "tint": self.Tint}


@dataclass(frozen=True)
class Catalog:
    Version: str
    DefaultMaterialId: str
    Groups: tuple
    Materials: dict           # id -> Material
    RingSizeSystem: str
    RingSizes: tuple

    def Get(self, MaterialId: str) -> Material | None:
        return self.Materials.get(MaterialId)

    def IsPurchasableGroup(self, GroupId: str) -> bool:
        return any(G["id"] == GroupId and G["purchasable"] for G in self.Groups)

    def IsValidSize(self, Size) -> bool:
        try:
            return float(Size) in self.RingSizes
        except (TypeError, ValueError):
            return False

    def ToJson(self) -> dict:
        return {
            "version": self.Version,
            "default_material_id": self.DefaultMaterialId,
            "groups": [
                {**G, "materials": [M.ToJson() for M in self.Materials.values() if M.Group == G["id"]]}
                for G in self.Groups
            ],
            "ring_sizes": {"system": self.RingSizeSystem, "values": list(self.RingSizes)},
        }


@lru_cache(maxsize=1)
def LoadCatalog() -> Catalog:
    Raw = json.loads((ConfigDir / "materials.json").read_text(encoding="utf-8"))
    Materials = {}
    for M in Raw["materials"]:
        Materials[M["id"]] = Material(
            Id=M["id"], Group=M["group"], Label=M["label"], CppMetal=M["cpp_metal"],
            DensityGCm3=float(M["density_g_cm3"]), RequiresPlating=bool(M["requires_plating"]),
            Swatch=M["swatch"], Tint=M["tint"])
    Cat = Catalog(
        Version=Raw["version"], DefaultMaterialId=Raw["default_material_id"],
        Groups=tuple(Raw["groups"]), Materials=Materials,
        RingSizeSystem=Raw["ring_sizes"]["system"],
        RingSizes=tuple(float(V) for V in Raw["ring_sizes"]["values"]))
    if Cat.DefaultMaterialId != "silver":
        raise ValueError("default_material_id must be 'silver' (confirmed product requirement)")
    return Cat
