"""Small draft translation helpers for manually editable German CV fields."""

from __future__ import annotations

import re


PHRASES = [
    ("Faculty Academic Appointments", "Akademische Berufungen"),
    ("Appointments at Hospitals/Affiliated Institutions", "Positionen an Kliniken und affiliierten Institutionen"),
    ("Other Professional Positions", "Weitere berufliche Positionen"),
    ("Committee Service", "Gremienarbeit"),
    ("Professional Societies", "Fachgesellschaften"),
    ("Grant Review Activities", "Gutachtertätigkeiten für Forschungsförderung"),
    ("Editorial Activities", "Editoriale Tätigkeiten"),
    ("Honors and Prizes", "Auszeichnungen und Preise"),
    ("Research Funding", "Forschungsförderung"),
    ("Funded and Unfunded Projects", "Forschungsförderung"),
    ("Teaching of Students in Courses", "Lehre in Kursen"),
    ("Research Supervisory and Training Responsibilities", "Betreuung und Ausbildung"),
    ("Invited Teaching and Presentations", "Eingeladene Lehre und Vorträge"),
    ("Clinical Activities and Innovations", "Klinische Tätigkeiten und Innovationen"),
    ("Teaching and Education Innovations", "Lehr- und Ausbildungsinnovationen"),
    ("Community Service", "Öffentlichkeitsarbeit"),
    ("Postdoctoral Training", "Postdoktorale Ausbildung"),
    ("Education", "Ausbildung"),
    ("Professor", "Professor"),
    ("Associate Professor", "Associate Professor"),
    ("Director", "Direktor"),
    ("Investigator", "Wissenschaftler"),
    ("Resident / Postdoctoral Fellow", "Assistenzarzt / Postdoktorand"),
    ("Postdoctoral Fellow", "Postdoktorand"),
    ("Junior Group Leader", "Nachwuchsgruppenleiter"),
    ("Emmy Noether Group Leader", "Emmy-Noether-Nachwuchsgruppenleiter"),
    ("Group Leader", "Gruppenleiter"),
    ("Faculty Affiliate", "Fakultätsmitglied"),
    ("Head, Network Stimulation Laboratory", "Leiter, Network Stimulation Laboratory"),
    ("Editorial Board Member", "Mitglied des Editorial Boards"),
    ("Associate Editor", "Associate Editor"),
    ("Handling Editor", "Handling Editor"),
    ("Guest Editor", "Gastherausgeber"),
    ("Editorial Author", "Editorial Author"),
    ("Editor Special Issue", "Herausgeber Sonderausgabe"),
    ("Ad hoc Reviewer", "Ad-hoc-Gutachter"),
    ("Committee member", "Kommissionsmitglied"),
    ("Organizer and Chair", "Organisator und Vorsitzender"),
    ("Scientific Advisory Board", "Wissenschaftlicher Beirat"),
    ("Student selection committee", "Auswahlkommission für Studierende"),
    ("PhD thesis committee", "Promotionskommission"),
    ("PhD Preliminary Qualifying Exam Committee", "Promotions-Vorprüfungskommission"),
    ("Organization for Human Brain Mapping", "Organization for Human Brain Mapping"),
    ("Member", "Mitglied"),
    ("Medicine", "Medizin"),
    ("Medical Neurosciences", "Medizinische Neurowissenschaften"),
    ("Neurology", "Neurologie"),
    ("Neurosurgery", "Neurochirurgie"),
    ("Movement Disorders", "Bewegungsstörungen"),
    ("Neuromodulation", "Neuromodulation"),
    ("Computational Neurology", "Computational Neurology"),
    ("Department", "Klinik"),
    ("Program", "Programm"),
    ("Prize", "Preis"),
    ("Award", "Auszeichnung"),
    ("Travel Grant", "Reisestipendium"),
    ("Seed Grant", "Anschubfinanzierung"),
    ("Fellowship", "Stipendium"),
    ("Scientific Achievements", "Wissenschaftliche Leistungen"),
    ("Early Career Recognition", "Auszeichnung für Nachwuchswissenschaftler"),
    ("Best PhD thesis", "Beste Promotion"),
    ("Last authorship", "Letztautorenschaft"),
    ("Publications", "Publikationen"),
    ("Software Publication", "Softwarepublikation"),
    ("Grant Call", "Förderaufruf"),
    ("Course", "Kurs"),
    ("Students", "Studierende"),
    ("PhD Students", "Promovierende"),
    ("Medical Students", "Medizinstudierende"),
    ("Supervisor", "Betreuer"),
    ("Supervision", "Betreuung"),
]


def draft_translate_to_german(text: str | None) -> str:
    """Return a conservative, manually editable German draft.

    This is intentionally a glossary-based prefill rather than a trusted final
    translation. Names, institutions, dates, and technical titles remain intact.
    """

    if not text:
        return ""
    translated = str(text)
    for english, german in PHRASES:
        translated = re.sub(re.escape(english), german, translated, flags=re.IGNORECASE)
    translated = translated.replace(" | ", " | ")
    translated = translated.replace("University Cologne", "Universität zu Köln")
    translated = translated.replace("University of Cologne", "Universität zu Köln")
    translated = translated.replace("University Hospital Cologne", "Uniklinik Köln")
    translated = translated.replace("German Research Foundation", "Deutsche Forschungsgemeinschaft")
    translated = translated.replace("European Research Council", "Europäischer Forschungsrat")
    translated = translated.replace("German Research Council", "Deutscher Forschungsrat")
    translated = translated.replace("Czech Science Foundation", "Tschechische Wissenschaftsstiftung")
    return translated


GERMAN_FIELD_PAIRS = [
    ("subcategory", "subcategory_de"),
    ("title", "title_de"),
    ("organization", "organization_de"),
    ("location", "location_de"),
    ("role", "role_de"),
    ("amount", "amount_de"),
    ("description", "description_de"),
    ("raw_text", "raw_text_de"),
]


def fill_german_drafts(row: dict) -> dict:
    out = dict(row)
    for english_field, german_field in GERMAN_FIELD_PAIRS:
        if not out.get(german_field):
            out[german_field] = draft_translate_to_german(out.get(english_field))
    return out
