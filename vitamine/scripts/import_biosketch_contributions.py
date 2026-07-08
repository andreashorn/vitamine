#!/usr/bin/env python3
"""Import current NIH biosketch contributions and link cited papers."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

from vitamine.paths import ROOT, active_db_path

DB = active_db_path()
SOURCE = ROOT / "background_docs" / "Biosketch_Horn.pdf"


CONTRIBUTIONS = [
    {
        "ordinal": 1,
        "title": "Defining Optimal Deep Brain Stimulation Targets for Parkinson Disease",
        "narrative": (
            "Our laboratory defined optimal treatment targets for deep brain stimulation in patients with Parkinson Disease in form of "
            "structural and functional networks. This work unites multiple stimulation sites (such as the subthalamic nucleus and "
            "internal pallidum) by demonstrating that modulating a specific network, into which these sites fall into, is key for "
            "treatment success. Using methods developed in our laboratory (see below), this work had a defining role in our "
            "understanding of pathophysiology and neuromodulative treatment of Parkinson Disease."
        ),
        "citations": [
            "Sobesky L, Goede L, Odekerken VJJ, Wang Q, Li N, Neudorfer C, Rajamani N, Al-Fatly B, Reich M, Volkmann J, de Bie RMA, Kühn AA, Horn A. Subthalamic and pallidal deep brain stimulation: are we modulating the same network? Brain. 2021 Aug 28. PMID: 34453827.",
            "Irmen F*, Horn A*, Mosley P, Perry A, Petry-Schmelzer JN, Dafsari HS, Barbe M, Visser-Vandewalle V, Schneider GH, Li N, Kübler D, Wenzel G, Kühn AA. Left Prefrontal Connectivity Links Subthalamic Stimulation with Depressive Symptoms. Annals of Neurology. 2020 Jun;87(6):962-975. PMID: 32239535.",
            "Horn A, Wenzel G, Irmen F, Huebl J, Li N, Neumann WJ, Krause P, Bohner G, Scheel M, Kühn AA. Deep brain stimulation induced normalization of the human functional connectome in Parkinson's disease. Brain. 2019 Oct 1;142(10):3129-3143. PMID: 31412106.",
            "Horn A, Reich M, Vorwerk J, Li N, Wenzel G, Fang Q, Schmitz-Hubsch T, Nickl R, Kupsch A, Volkmann J, Kühn AA, Fox MD. Connectivity Predicts deep brain stimulation outcome in Parkinson disease. Ann Neurol. 2017;82(1):67-78. PMID: 28586141.",
        ],
    },
    {
        "ordinal": 2,
        "title": "Toward Connectomic Deep Brain Stimulation",
        "narrative": (
            "As a result of work in both Deep Brain Stimulation (DBS) and human connectomics, my current focus is on "
            "connectivity-based analysis and optimization of neuromodulation. I contributed multiple papers that were first to "
            "predict clinical outcomes following deep brain stimulation across multiple DBS centers and neurosurgeons based "
            "structural and functional brain connectivity. I used brain connectivity in combination with electrophysiological data "
            "to shed light into the occurrence of elevated beta activity in Parkinson’s disease that is now used as a physiomarker "
            "for closed-loop applications. I personally think that the combination of the connectome framework with brain "
            "stimulation will become a strong and important field of neuroimaging in the future. This work culminated in a "
            "handbook entitled Connectomic Deep Brain Stimulation published with Elsevier in fall 2021."
        ),
        "citations": [
            "Hollunder B, Ostrem JL, Sahin IA, Rajamani N, Oxenford S, Butenko K, Neudorfer C, Reinhardt P, Zvarova P, Polosan M, Akram H, Vissani M, Zhang C, Sun B, Navratil P, Reich MM, Volkmann J, Yeh FC, Baldermann JC, Dembek TA, Visser-Vandewalle V, Alho EJL, Franceschini PR, Nanda P, Finke C, Kühn AA, Dougherty DD, Richardson RM, Bergman H, DeLong MR, Mazzoni A, Romito LM, Tyagi H, Zrinzo L, Joyce EM, Chabardes S, Starr PA, Li N, Horn A. Mapping dysfunctional circuits in the frontal cortex using deep brain stimulation. Nature Neuroscience. 2024 Mar;27(3):573-586. PMID: 38388734.",
            "Li N, Baldermann JC, Kibleur A, Treu S, Akram H, Elias GJB, Boutet A, Lozano AM, Al-Fatly B, Strange B, Barcia JA, Zrinzo L, Joyce E, Chabardes S, Visser-Vandewalle V, Polosan M, Kuhn J, Kühn AA, Horn A. A unified connectomic target for deep brain stimulation in obsessive-compulsive disorder. Nature Communications. 2020 Jul 3;11(1):3364. PMID: 32620886.",
            "Li N, Hollunder B, Baldermann JC, Kibleur A, Treu S, Akram H, Al-Fatly B, Strange BA, Barcia JA, Zrinzo L, Joyce EM, Chabardes S, Visser-Vandewalle V, Polosan M, Kuhn J, Kühn AA, Horn A. A Unified Functional Network Target for Deep Brain Stimulation in Obsessive-Compulsive Disorder. Biological Psychiatry. 2021 Apr 20; PMID: 34134839.",
            "Baldermann JC, Schüller T, Kohl S, Voon V, Li N, Hollunder B, Figee M, Haber SN, Sheth SA, Mosley PE, Huys D, Johnson KA, Butson C, Ackermans L, Bouwens van der Vlis T, Leentjens AFG, Barbe M, Visser-Vandewalle V, Kuhn J, Horn A. Connectomic Deep Brain Stimulation for Obsessive-Compulsive Disorder. Biological Psychiatry. 2021 Jul 19;. PMID: 34482949.",
        ],
    },
    {
        "ordinal": 3,
        "title": "Establishing and validating a normative wiring diagram of the human brain",
        "narrative": (
            "I was first to define a normative structural wiring diagram within standardized stereotactic space during my PhD in 2015. "
            "In a team led by Prof. Michael Fox in Boston, I extended this line of research to define a normative functional "
            "connectome in standard space. The resulting datasets that have now been estimated and validated on various normative "
            "and disease populations could be considered as high-resolution atlases defining the degree of interconnection between "
            "each region of the human brain. Over the years, my laboratory created multiple refined connectomes, sometimes of the "
            "healthy human brain but also on a patient population (such as depression or Parkinson’s Disease). The technique and "
            "generated data offer a unique approach that allows to analyze brain connectivity in situations where subject- or "
            "patient-specific connectivity datasets are not available. Especially in clinical populations (brain stimulation, stroke, "
            "or MS lesions), where such patient specific data is not available, the approach offers a crucial opportunity to study "
            "distributed effects of lesions or stimulation sites on brain networks."
        ),
        "citations": [
            "Horn A, Reich MM, Ewert S, Li N, Al-Fatly B, Lange F, Roothans J, Oxenford S, Horn I, Paschen S, Runge J, Wodarg F, Witt K, Nickl RC, Wittstock M, Schneider GH, Mahlknecht P, Poewe W, Eisner W, Helmers AK, Matthies C, Krauss JK, Deuschl G, Volkmann J, Kühn AA. Optimal deep brain stimulation sites and networks for cervical vs. generalized dystonia. Proc Natl Acad Sci USA. 2022;119(14):e2114985119. PMID: 35357970.",
            "Ganos C, Al-Fatly B, Fischer JF, Baldermann JC, Hennen C, Visser-Vandewalle V, Neudorfer C, Martino D, Li J, Bouwens T, Ackermanns L, Leentjens AFG, Pyatigorskaya N, Worbe Y, Fox MD, Kühn AA, Horn A. A neural network for tics: insights from causal brain lesions and deep brain stimulation. Brain. 2022:awac009. PMID: 35026844.",
            "Hollunder B, Rajamani N, Siddiqi SH, Finke C, Kühn AA, Mayberg HS, Fox MD, Neudorfer C, Horn A. Toward personalized medicine in connectomic deep brain stimulation. Progress in Neurobiology. 102211. PMID: 34958874.",
            "Baldermann JC, Melzer C, Zapf A, Kohl S, Timmermann L, Tittgemeyer M, Huys D, Visser-Vandewalle V, Kühn AA, Horn A*, Kuhn J*. Connectivity Profile Predictive of Effective Deep Brain Stimulation in Obsessive-Compulsive Disorder. Biological Psychiatry. 2019 May 1;85(9):735-743. PMID: 30777287.",
        ],
    },
    {
        "ordinal": 4,
        "title": "Development of a software toolbox for deep brain stimulation imaging",
        "narrative": (
            "I created an open-source software pipeline that generates virtual patient models in which researchers can study the local "
            "and global effects of deep brain stimulation. The software, Lead-DBS, has become the major platform for DBS imaging "
            "analyses and has been used in >500 research articles from teams on all continents since 2014. The continuing "
            "development of Lead-DBS both shapes our own research projects which often combine methods development with addressing "
            "translational research questions in neurology and psychiatry."
        ),
        "citations": [
            "Horn A, Reich M, Vorwerk J, Li N, Wenzel G, Fang Q, Schmitz-Hübsch T, Nickl R, Kupsch A, Volkmann J, Kühn AA, Fox MD. Connectivity Predicts deep brain stimulation outcome in Parkinson disease. Annals of Neurology. 2017 Jul;82(1):67-78. PMID: 28586141.",
            "Reich MM*, Horn A*, Lange F, Roothans J, Paschen S, Runge J, Wodarg F, Pozzi NG, Witt K, Nickl RC, Soussand L, Ewert S, Maltese V, Wittstock M, Schneider GH, Coenen V, Mahlknecht P, Poewe W, Eisner W, Helmers AK, Matthies C, Sturm V, Isaias IU, Krauss JK, Kühn AA, Deuschl G, Volkmann J. Probabilistic mapping of the antidystonic effect of pallidal neurostimulation: a multicentre imaging study. Brain. 2019 May 1;142(5):1386-1398. PMID: 30851091.",
            "Neudorfer C, Kroneberg D, Al-Fatly B, Goede L, Kübler D, Faust K, Rienen U, Tietze A, Picht T, Herrington TM, Middlebrooks EH, Kühn A, Schneider G, Horn A. Personalizing Deep Brain Stimulation Using Advanced Imaging Sequences. Annals of Neurology. 2022;91(5):613-628. PMID: 35165921.",
            "Oxenford S, Roediger J, Neudorfer C, Milosevic L, Güttler C, Spindler P, Vajkoczy P, Neumann WJ, Kühn AA, Horn A. Lead-OR: a multimodal platform for deep brain stimulation surgery. eLife. 2022;11:e72929. PMID: 35594135.",
        ],
    },
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS biosketch_contribution_publications (
          id INTEGER PRIMARY KEY,
          contribution_id INTEGER NOT NULL REFERENCES biosketch_contributions(id) ON DELETE CASCADE,
          citation_label TEXT NOT NULL,
          publication_id INTEGER REFERENCES publications(id) ON DELETE SET NULL,
          raw_citation TEXT NOT NULL,
          pmid TEXT,
          doi TEXT,
          UNIQUE(contribution_id, citation_label)
        )
        """
    )
    return con


def pmid_from_citation(citation: str) -> str:
    match = re.search(r"PMID:\s*(\d+)", citation)
    return match.group(1) if match else ""


def link_publication(con: sqlite3.Connection, citation: str) -> sqlite3.Row | None:
    pmid = pmid_from_citation(citation)
    if pmid:
        row = con.execute("SELECT id, doi FROM publications WHERE pmid=? ORDER BY suppress_display, id LIMIT 1", (pmid,)).fetchone()
        if row:
            return row
    return None


def import_contributions() -> dict[str, int]:
    with connect() as con:
        con.execute(
            """
            INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
            VALUES ('biosketch_horn_pdf_current', 'Current NIH biosketch PDF', ?, 'pdf', ?, 'Contribution narratives and citations imported from the current biosketch PDF.')
            ON CONFLICT(slug) DO UPDATE SET imported_at=excluded.imported_at, source_path=excluded.source_path
            """,
            (str(SOURCE.relative_to(ROOT)), dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        document_id = int(con.execute("SELECT id FROM documents WHERE slug='biosketch_horn_pdf_current'").fetchone()[0])
        con.execute("DELETE FROM biosketch_contribution_publications")
        con.execute("DELETE FROM biosketch_contributions")
        linked = 0
        for contribution in CONTRIBUTIONS:
            citations = [f"{chr(97 + index)}. {citation}" for index, citation in enumerate(contribution["citations"])]
            cur = con.execute(
                """
                INSERT INTO biosketch_contributions (document_id, ordinal, title, narrative, citations_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    contribution["ordinal"],
                    contribution["title"],
                    contribution["narrative"],
                    json.dumps(citations, ensure_ascii=False),
                ),
            )
            contribution_id = int(cur.lastrowid)
            for index, citation in enumerate(contribution["citations"]):
                label = chr(97 + index)
                publication = link_publication(con, citation)
                if publication:
                    linked += 1
                con.execute(
                    """
                    INSERT INTO biosketch_contribution_publications
                      (contribution_id, citation_label, publication_id, raw_citation, pmid, doi)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contribution_id,
                        label,
                        publication["id"] if publication else None,
                        citation,
                        pmid_from_citation(citation),
                        publication["doi"] if publication else None,
                    ),
                )
        con.commit()
    return {"contributions": len(CONTRIBUTIONS), "citations": sum(len(item["citations"]) for item in CONTRIBUTIONS), "linked": linked}


if __name__ == "__main__":
    print(json.dumps(import_contributions(), indent=2, ensure_ascii=False))
