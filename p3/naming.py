"""Deterministic product names ("The Laurel Ring"), no LLM call.

Port of Pipeline 2's app-p2.js generateProductName / _extractEntity /
_nameStyle / _styleSuffix (revision 1e871734), moved server-side so the saved
design title and the displayed name are the same (P2 kept them separate).
"""

import re

FallbackNames = [
    "The Lumière Ring", "The Solène Ring", "The Aurora Ring",
    "The Luna Ring", "The Solstice Ring", "The Aria Ring",
    "The Céleste Ring", "The Ondine Ring", "The Éclat Ring",
    "The Vesper Ring", "The Orion Ring", "The Nova Ring",
]

Motifs = [
    (r"serpent|snake|python|cobra|viper|ophid", "Serpent"),
    (r"dragon|drake|wyvern", "Drake"),
    (r"phoenix|firebird", "Phoenix"),
    (r"flame|fire|ember|blaz", "Ember"),
    (r"wave|ocean|\bsea\b|tide|aqua|marine|nautic", "Marée"),
    (r"rose|floral|flower|petal|bloom|botanic", "Rose"),
    (r"leaf|vine|\bivy\b|laurel|olive|branch", "Laurel"),
    (r"star|stellar|celestial|cosmos|galaxy", "Nova"),
    (r"moon|lunar|crescent", "Luna"),
    (r"\bsun\b|solar|sunburst|radian", "Solène"),
    (r"heart|amour", "Amour"),
    (r"skull|gothic|raven|\bnoir\b", "Noir"),
    (r"crown|regal|royal|\bking\b|\bqueen\b", "Régent"),
    (r"knot|infinity|eternal|forever", "Éternité"),
    (r"feather|\bwing\b|angel|seraph", "Séraphine"),
    (r"butterfly|papillon", "Papillon"),
    (r"tiger|\blion\b|panther|leopard|jaguar", "Fauve"),
    (r"\bbee\b|honey|\bhive\b", "Abeille"),
]

_Stop = set((
    "the a an and or of for with to in on at by from into onto que la le "
    "create process design designed preserve keep maintain possible introduce make making "
    "featuring inspired attached reference ref image images photo picture model models figure figures "
    "ring rings gold silver metal metallic platinum luxury premium elegant style final copy edit new "
    "please your this that these those world cup detailed suitable precious original concept collection "
    "signet solitaire halo eternity band wrap coil tapered articulated sleek minimal delicate "
    "dainty bold chunky statement diamond brilliant gem sapphire emerald ruby").split())


def _StyleSuffix(Lower: str, Core: str) -> str:
    if re.search(r"\bsignet\b", Lower) and "signet" not in Core.lower():
        return Core + " Signet"
    return Core


def _ExtractEntity(Prompt: str) -> str:
    Hero = ""
    for Index, Tok in enumerate(re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ']{2,}", Prompt)):
        if Index == 0 or not re.match(r"[A-ZÀ-Þ]", Tok) or Tok.lower() in _Stop:
            continue
        Hero = Tok
    return Hero[0].upper() + Hero[1:].lower() if Hero else ""


def _NameStyle(Lower: str) -> str | None:
    Rules = [
        (r"\bsignet\b", "Signet"), (r"\bsolitaire\b", "Solitaire"), (r"\bhalo\b", "Halo"),
        (r"eternity|pav[eé]|full band", "Éternité"), (r"wrap|coil|articulat", "Coil"),
        (r"allong|elongat|lengthen|\blong\b|tapered|\bslim\b", "Élan"),
        (r"minimal|delicate|dainty|\bthin\b|\bfine\b", "Fil"),
        (r"bold|chunky|statement|\bthick\b", "Bold"),
        (r"diamond|brilliant|\bgem\b|sapphire|emerald|ruby", "Éclat"),
    ]
    for Pattern, Core in Rules:
        if re.search(Pattern, Lower):
            return Core
    return None


Roman = ["", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]


def UniqueTitle(Db, Base: str) -> str:
    """A design name no other design carries (case-insensitive): "The Fil Ring", then "The Fil Ring II",
    "The Fil Ring III" … Names are how people talk about rings, so two rings never share one."""
    Base = " ".join((Base or "Ring").split())
    for N in range(1, 400):
        Candidate = Base if N == 1 else f"{Base} {Roman[N - 1] if N - 1 < len(Roman) else N}"
        if not Db.One("SELECT 1 AS x FROM designs WHERE lower(title) = lower(?)", (Candidate,)):
            return Candidate
    return f"{Base} {Db.One('SELECT COUNT(*) AS n FROM designs')['n'] + 1}"


def VariationTitle(Db, MasterTitle: str) -> str:
    """A customer's refinement of a gallery master is a design of its own: "<Master> Variation" (unique)."""
    Base = MasterTitle or "Ring"
    return UniqueTitle(Db, Base if Base.endswith("Variation") else f"{Base} Variation")


def ProductName(Prompt: str) -> str:
    Lower = Prompt.lower()
    Wrap = lambda Core: "The " + _StyleSuffix(Lower, Core) + " Ring"
    for Pattern, Core in Motifs:
        if re.search(Pattern, Lower):
            return Wrap(Core)
    Entity = _ExtractEntity(Prompt)
    if Entity:
        return Wrap(Entity)
    Style = _NameStyle(Lower)
    if Style:
        return Wrap(Style)
    Seed = Prompt.strip() or "ring"
    Hash = 0
    for Ch in Seed:
        Hash = (Hash + ord(Ch)) % len(FallbackNames)
    return FallbackNames[Hash]
