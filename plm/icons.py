"""Pictogrammes SVG illustratifs par type de composant.

L'export OEM ne contient aucune géométrie (WithFiles = No) : ces dessins ne sont PAS la
pièce réelle, seulement un repère visuel sur sa nature (roulement, engrenage, carter…).
Si l'OEM fournit des fichiers 3D (STEP, 3DXML), on peut les remplacer par de vraies vignettes.
"""
from __future__ import annotations

import re

_C = {"ink": "#1c2430", "blue": "#1f6feb", "soft": "#e3eefc", "muted": "#8a97ab", "metal": "#c9d2de", "copper": "#c77d00", "warn": "#c77d00"}

_SVG = {
    "bearing": '<circle cx="50" cy="50" r="40" fill="none" stroke="{ink}" stroke-width="6"/><circle cx="50" cy="50" r="16" fill="none" stroke="{ink}" stroke-width="6"/>'
               + "".join(f'<circle cx="{50 + 28 * __import__("math").cos(a * 3.1416 / 4):.1f}" cy="{50 + 28 * __import__("math").sin(a * 3.1416 / 4):.1f}" r="5" fill="{{blue}}"/>' for a in range(8)),
    "gear": '<path d="M50 8l6 10 11-4 2 12 12 2-4 11 10 6-10 6 4 11-12 2-2 12-11-4-6 10-6-10-11 4-2-12-12-2 4-11-10-6 10-6-4-11 12-2 2-12 11 4z" fill="{soft}" stroke="{ink}" stroke-width="4" stroke-linejoin="round"/><circle cx="50" cy="50" r="12" fill="#fff" stroke="{ink}" stroke-width="4"/>',
    "shaft": '<rect x="8" y="42" width="84" height="16" rx="3" fill="{metal}" stroke="{ink}" stroke-width="4"/><rect x="30" y="34" width="14" height="32" rx="2" fill="{soft}" stroke="{ink}" stroke-width="4"/><rect x="60" y="36" width="10" height="28" rx="2" fill="{soft}" stroke="{ink}" stroke-width="4"/>',
    "housing": '<path d="M14 70V38l20-16h32l20 16v32z" fill="{soft}" stroke="{ink}" stroke-width="4" stroke-linejoin="round"/><circle cx="50" cy="52" r="12" fill="#fff" stroke="{ink}" stroke-width="4"/><circle cx="24" cy="64" r="3" fill="{ink}"/><circle cx="76" cy="64" r="3" fill="{ink}"/><circle cx="24" cy="42" r="3" fill="{ink}"/><circle cx="76" cy="42" r="3" fill="{ink}"/>',
    "screw": '<rect x="42" y="12" width="16" height="14" rx="2" fill="{metal}" stroke="{ink}" stroke-width="4"/><rect x="45" y="26" width="10" height="56" fill="{metal}" stroke="{ink}" stroke-width="4"/>' + "".join(f'<line x1="45" y1="{y}" x2="55" y2="{y + 4}" stroke="{{ink}}" stroke-width="2.5"/>' for y in range(34, 80, 8)),
    "stator": '<circle cx="50" cy="50" r="42" fill="{metal}" stroke="{ink}" stroke-width="4"/><circle cx="50" cy="50" r="20" fill="#fff" stroke="{ink}" stroke-width="4"/>' + "".join(f'<rect x="47" y="8" width="6" height="22" fill="{{soft}}" stroke="{{ink}}" stroke-width="2" transform="rotate({a} 50 50)"/>' for a in range(0, 360, 30)),
    "coil": '<circle cx="50" cy="50" r="42" fill="none" stroke="{ink}" stroke-width="4"/><circle cx="50" cy="50" r="22" fill="none" stroke="{ink}" stroke-width="4"/>' + "".join(f'<ellipse cx="50" cy="18" rx="7" ry="10" fill="{{copper}}" stroke="{{ink}}" stroke-width="2" transform="rotate({a} 50 50)"/>' for a in range(0, 360, 30)),
    "tooth": '<path d="M35 85V35q15-25 30 0v50z" fill="{metal}" stroke="{ink}" stroke-width="4" stroke-linejoin="round"/><rect x="47" y="50" width="6" height="35" fill="{soft}"/>',
    "cooling": '<rect x="14" y="26" width="72" height="48" rx="6" fill="{soft}" stroke="{ink}" stroke-width="4"/>' + "".join(f'<line x1="{x}" y1="30" x2="{x}" y2="70" stroke="{{blue}}" stroke-width="3"/>' for x in range(24, 80, 9)),
    "pipe": '<path d="M12 36h40a12 12 0 0 1 12 12v40" fill="none" stroke="{ink}" stroke-width="14" stroke-linecap="round"/><path d="M12 36h40a12 12 0 0 1 12 12v40" fill="none" stroke="{soft}" stroke-width="7" stroke-linecap="round"/>',
    "seal": '<circle cx="50" cy="50" r="40" fill="{ink}" opacity=".85"/><circle cx="50" cy="50" r="30" fill="{warn}"/><circle cx="50" cy="50" r="18" fill="#fff"/>',
    "differential": '<circle cx="50" cy="50" r="30" fill="{soft}" stroke="{ink}" stroke-width="4"/><rect x="6" y="45" width="30" height="10" fill="{metal}" stroke="{ink}" stroke-width="3"/><rect x="64" y="45" width="30" height="10" fill="{metal}" stroke="{ink}" stroke-width="3"/><circle cx="50" cy="50" r="10" fill="#fff" stroke="{ink}" stroke-width="3"/>',
    "pin": '<rect x="44" y="10" width="12" height="80" rx="4" fill="{metal}" stroke="{ink}" stroke-width="4"/>',
    "plate": '<rect x="14" y="30" width="72" height="40" rx="4" fill="{metal}" stroke="{ink}" stroke-width="4"/><circle cx="26" cy="42" r="3" fill="{ink}"/><circle cx="74" cy="42" r="3" fill="{ink}"/><circle cx="26" cy="58" r="3" fill="{ink}"/><circle cx="74" cy="58" r="3" fill="{ink}"/>',
    "motor": '<rect x="16" y="28" width="56" height="44" rx="8" fill="{soft}" stroke="{ink}" stroke-width="4"/><rect x="72" y="44" width="18" height="12" fill="{metal}" stroke="{ink}" stroke-width="3"/>' + "".join(f'<line x1="{x}" y1="32" x2="{x}" y2="68" stroke="{{ink}}" stroke-width="2"/>' for x in range(26, 66, 8)),
    "assembly": '<rect x="10" y="22" width="34" height="26" rx="4" fill="{soft}" stroke="{ink}" stroke-width="3"/><rect x="56" y="22" width="34" height="26" rx="4" fill="{soft}" stroke="{ink}" stroke-width="3"/><rect x="33" y="56" width="34" height="26" rx="4" fill="{soft}" stroke="{ink}" stroke-width="3"/><path d="M27 48v8h46v-8M50 56v-8" fill="none" stroke="{muted}" stroke-width="3"/>',
    "generic": '<rect x="20" y="20" width="60" height="60" rx="8" fill="{soft}" stroke="{ink}" stroke-width="4"/><text x="50" y="58" text-anchor="middle" font-size="22" fill="{ink}" font-family="Arial">?</text>',
}

_RULES = [
    (r"bea\d|bearing housing|roulement", "bearing"), (r"gear|bevel|engren", "gear"), (r"\bhsh\b|\bish\b|\blsh\b|shaft|arbre", "shaft"),
    (r"screw|vis\b", "screw"), (r"stator core tooth|tooth", "tooth"), (r"stator core|stator\b$", "stator"), (r"coil", "coil"),
    (r"cooling", "cooling"), (r"pipe", "pipe"), (r"spy|joint|seal", "seal"), (r"differential_assembly|differential_casting|differential$", "differential"),
    (r"\bpin\b", "pin"), (r"plate", "plate"), (r"^motor$|\bmotor\b", "motor"), (r"housing|carter|support", "housing"),
]


def kind_of(name: str, role: str = "part") -> str:
    n = name.lower()
    if role == "assembly":
        if "stator" in n:
            return "stator"
        if "differential" in n:
            return "differential"
        return "assembly"
    for pat, k in _RULES:
        if re.search(pat, n):
            return k
    return "generic"


def svg(name: str, role: str = "part", size: int = 96) -> str:
    body = _SVG[kind_of(name, role)].format(**_C)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="{size}" height="{size}" role="img" aria-label="Pictogramme {kind_of(name, role)}">{body}</svg>'


LABEL = {"bearing": "Roulement", "gear": "Engrenage", "shaft": "Arbre", "housing": "Carter / support", "screw": "Visserie", "stator": "Stator",
         "coil": "Bobinage", "tooth": "Dent de stator", "cooling": "Refroidissement", "pipe": "Tuyau", "seal": "Joint", "differential": "Différentiel",
         "pin": "Axe", "plate": "Plaque", "motor": "Carter moteur", "assembly": "Assemblage", "generic": "Composant"}
