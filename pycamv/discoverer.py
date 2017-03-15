"""
Provides functionality for interacting with ProteomeDiscoverer data.
"""

from __future__ import absolute_import, division

from collections import Counter
import logging
import os
import re
import sqlite3

from . import search


LOGGER = logging.getLogger("pycamv.discoverer")

RE_DESCRIPTION = re.compile(r"^>sp\|[\dA-Za-z]+\|([\dA-Za-z_]+) (.*)$")


def _parse_letters(letters):
    """
    Turns a string of residue letters (i.e. "STY") into a list.

    Includes special provisions for N- and C-term modifications.

    Parameters
    ----------
    letters : str

    Returns
    -------
    tuple of str
    """
    if letters in ["N-term", "C-term"]:
        return (letters,)

    return tuple(letters)


def _get_fixed_var_mods(cursor):
    query = cursor.execute(
        """
        SELECT
        ProcessingNodeParameters.ParameterName,
        ProcessingNodeParameters.ParameterValue
        FROM
        ProcessingNodeParameters
        """,
    )

    fixed_mods = []
    var_mods = []

    for name, val in query:
        if name.startswith("StaticMod_"):
            fixed_mods.append(val)
        elif name.startswith("DynMod_"):
            var_mods.append(val)

    return fixed_mods, var_mods


def _find_mod(abbrev, letter, pot_mods):
    for pot_mod in pot_mods:
        if pot_mod[0] == abbrev and letter in pot_mod[1]:
            return pot_mod

    return None


def _count_mods(mod_list):
    return [
        (count, abbrev, _parse_letters(letters))
        for (abbrev, letters), count in Counter(mod_list).items()
    ]


def _get_pep_mods(cursor, pep_id, pep_seq, var_mods, fixed_mods):
    cursor = cursor.connection.cursor()
    aa_mods = cursor.execute(
        """
        SELECT
        AminoAcidModifications.ModificationName,
        PeptidesAminoAcidModifications.Position
        FROM
        PeptidesAminoAcidModifications JOIN
        AminoAcidModifications
        WHERE
        PeptidesAminoAcidModifications.PeptideID=:pepID AND
        PeptidesAminoAcidModifications.AminoAcidModificationID=
        AminoAcidModifications.AminoAcidModificationID
        """,
        {
            "pepID": pep_id,
        }
    )

    pep_var_mods = []
    pep_fixed_mods = []

    for abbrev, pos in aa_mods:
        letter = pep_seq[pos]
        mod = _find_mod(abbrev, letter, var_mods)

        if mod:
            pep_var_mods.append(mod)
            continue

        mod = _find_mod(abbrev, letter, fixed_mods)

        if mod:
            pep_fixed_mods.append(mod)
            continue

        raise Exception(
            "Unexpected modification: {} {}".format(letter, abbrev)
        )

    term_mods = cursor.execute(
        """
        SELECT
        AminoAcidModifications.ModificationName,
        AminoAcidModifications.PositionType
        FROM
        PeptidesTerminalModifications JOIN
        AminoAcidModifications
        WHERE
        PeptidesTerminalModifications.PeptideID=:pepID AND
        PeptidesTerminalModifications.TerminalModificationID=
        AminoAcidModifications.AminoAcidModificationID
        """,
        {
            "pepID": pep_id,
        }
    )

    for abbrev, pos_type in term_mods:
        letter = "N-term" if pos_type == 1 else "C-term"
        mod = _find_mod(abbrev, letter, var_mods)

        if mod:
            pep_var_mods.append(mod)
            continue

        mod = _find_mod(abbrev, letter, fixed_mods)

        if mod:
            pep_fixed_mods.append(mod)
            continue

        raise Exception(
            "Unexpected modification: {} {}".format(letter, abbrev)
        )

    pep_var_mods = _count_mods(pep_var_mods)
    pep_fixed_mods = _count_mods(pep_fixed_mods)

    # print(pep_var_mods)

    return pep_var_mods, pep_fixed_mods


def _get_peptide_queries(cursor, fixed_mods, var_mods):
    out = []
    # index = 0
    # scan_used = {}
    fixed_mods = [
        search.RE_DYN_MODS.match(i).group(3, 4)
        for i in fixed_mods
    ]
    var_mods = [
        search.RE_DYN_MODS.match(i).group(3, 4)
        for i in var_mods
    ]

    query = cursor.execute(
        """
        SELECT
        Peptides.PeptideID,
        ProteinAnnotations.Description,
        Peptides.Sequence,
        SpectrumHeaders.FirstScan,
        Masspeaks.Mass,
        Masspeaks.Charge,
        FileInfos.FileName
        FROM
        Peptides JOIN
        PeptidesProteins JOIN
        ProteinAnnotations JOIN
        SpectrumHeaders JOIN
        FileInfos JOIN
        Masspeaks
        WHERE
        Peptides.SearchEngineRank=1 AND
        Peptides.PeptideID=PeptidesProteins.PeptideID AND
        ProteinAnnotations.ProteinID=PeptidesProteins.ProteinID AND
        SpectrumHeaders.SpectrumID=Peptides.SpectrumID AND
        Masspeaks.MassPeakID=SpectrumHeaders.MassPeakID AND
        FileInfos.FileID=MassPeaks.FileID
        """
    )

    for (
        pep_id, full_prot_desc, pep_seq, scan, exp_mz, exp_z, filename,
    ) in query:
        # print(pep_id, full_prot_desc, pep_seq, scan, exp_mz, exp_z, filename)
        pep_var_mods, pep_fixed_mods = _get_pep_mods(
            cursor, pep_id, pep_seq, var_mods, fixed_mods,
        )

        accession, prot_desc = RE_DESCRIPTION.match(full_prot_desc).group(1, 2)

        out.append(
            search.PeptideQuery(
                accession,
                prot_desc,
                query,
                filename,
                # rank,
                # pep_score,
                exp_mz,
                exp_z,
                pep_seq,
                pep_var_mods,
                pep_fixed_mods,
                scan,
            )
        )
        # print(pep_id, pep_seq, pep_var_mods)

    out = sorted(out, key=lambda x: x.scan)

    return out


def read_discoverer_msf(msf_path):
    """
    Parse a ProteomeDiscoverer MSF file.

    Parameters
    ----------
    msf_path : str
        Path to MSF file.

    Returns
    -------
    fixed_mods : list of str
    var_mods : list of str
    out : list of :class:`PeptideQuery<pycamv.search.PeptideQuery>`
    """
    LOGGER.info(
        "Loading ProteomeDiscoverer peptides from \"{}\"".format(
            os.path.basename(msf_path),
        )
    )

    with sqlite3.connect(msf_path) as conn:
        cursor = conn.cursor()

        fixed_mods, var_mods = _get_fixed_var_mods(cursor)
        out = _get_peptide_queries(cursor, fixed_mods, var_mods)

    # print(fixed_mods, var_mods, out)

    return fixed_mods, var_mods, out