"""The county's approved protective-action library.

Every protective action an alert recommends must come from this list. The
structure follows the Warning Lexicon research (Sutton, Olson and Waugh, 2024,
Natural Hazards Review, doi:10.1061/NHREFO.NHENG-1900): a complete warning names
its source, the hazard, the location, the time, and what to do. The wording
below is this project's own plain-language text, in English and Spanish.
"""
from __future__ import annotations

ACTIONS: dict[str, dict] = {
    "flood-higher-ground": {"events": ["FFW", "FLW", "FFS"], "en": "Move to higher ground now.",
                            "es": "Vaya a un lugar alto ahora."},
    "flood-no-driving": {"events": ["FFW", "FLW", "FFS"], "en": "Do not drive or walk into flood water.",
                         "es": "No maneje ni camine en agua de inundacion."},
    "flood-avoid-crossings": {"events": ["FFW", "FLW"], "en": "Avoid low water crossings and river banks.",
                              "es": "Evite cruces bajos y orillas de rios."},
    "tornado-shelter": {"events": ["TOR"], "en": "Go to a basement or an inside room on the lowest floor now.",
                        "es": "Vaya a un sotano o cuarto interior en el piso mas bajo ahora."},
    "tornado-vehicle": {"events": ["TOR"], "en": "If in a vehicle or mobile home, get to a sturdy building.",
                        "es": "Si esta en un vehiculo o casa movil, vaya a un edificio solido."},
    "storm-indoors": {"events": ["SVR", "EWW", "HWW"], "en": "Stay indoors and away from windows.",
                      "es": "Quedese adentro y lejos de las ventanas."},
    "hurricane-evacuate": {"events": ["HUW", "SSW", "EVI"], "en": "Leave now if you are in an evacuation zone.",
                           "es": "Salga ahora si esta en una zona de evacuacion."},
    "fire-evacuate": {"events": ["FRW", "EVI"], "en": "Leave now. Follow the evacuation route given by officials.",
                      "es": "Salga ahora. Siga la ruta de evacuacion indicada."},
    "fire-get-ready": {"events": ["FRW"], "en": "Get ready to leave. Pack medicine, documents and pets.",
                       "es": "Preparese para salir. Lleve medicinas, documentos y mascotas."},
    "shelter-in-place": {"events": ["SPW", "CDW", "HMW"], "en": "Go inside, close windows and doors, and stay inside.",
                         "es": "Entre, cierre puertas y ventanas, y quedese adentro."},
    "tsunami-move-inland": {"events": ["TSW"], "en": "Move inland or to high ground now.",
                            "es": "Vaya tierra adentro o a un lugar alto ahora."},
    "winter-stay-off-roads": {"events": ["BZW", "WSW", "ISW"], "en": "Stay off the roads.",
                              "es": "No salga a las carreteras."},
    "monitor-official": {"events": ["*"], "en": "Check official updates from the county.",
                         "es": "Siga las noticias oficiales del condado."},
}

# Words that name each hazard, used to confirm the alert names the same hazard as its source.
HAZARD_TERMS: dict[str, list[str]] = {
    "FFW": ["flash flood"], "FLW": ["flood"], "FFS": ["flash flood"], "TOR": ["tornado"],
    "SVR": ["thunderstorm", "storm"], "EWW": ["wind"], "HWW": ["wind"], "HUW": ["hurricane"],
    "SSW": ["storm surge", "surge"], "FRW": ["fire"], "EVI": ["evacuat"], "CDW": ["danger", "threat"],
    "CEM": ["emergency"], "SPW": ["shelter"], "TSW": ["tsunami"], "BZW": ["blizzard"],
    "WSW": ["winter storm"], "ISW": ["ice"], "HMW": ["hazardous material", "chemical"],
}
HAZARD_TERMS_ES: dict[str, list[str]] = {
    "FFW": ["inundaci"], "FLW": ["inundaci"], "TOR": ["tornado"], "SVR": ["tormenta"],
    "FRW": ["incendio", "fuego"], "EVI": ["evacua"], "HUW": ["huracan"], "TSW": ["tsunami"],
}

SOURCE_TERMS = ["nws", "national weather service", "weather service", "emergency management",
                "sheriff", "fire department", "county", "condado", "servicio nacional", "snm"]


def actions_for(event_code: str) -> dict[str, dict]:
    return {k: v for k, v in ACTIONS.items() if event_code in v["events"] or "*" in v["events"]}
