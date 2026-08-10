HEADERS = {
    "es": {
        "summary": "Resumen Profesional",
        "experience": "Experiencia Laboral",
        "education": "Educación",
        "skills": "Habilidades",
        "languages": "Idiomas",
        "certifications": "Certificaciones",
    },
    "en": {
        "summary": "Professional Summary",
        "experience": "Work Experience",
        "education": "Education",
        "skills": "Skills",
        "languages": "Languages",
        "certifications": "Certifications",
    },
}


def get_headers(lang: str) -> dict[str, str]:
    return HEADERS.get(lang, HEADERS["en"])


def supported_langs() -> tuple[str, ...]:
    """Languages the tool has section headers for."""
    return tuple(HEADERS)
