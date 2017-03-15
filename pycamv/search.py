"""
Provides functionality for interacting with MASCOT data.
"""

from __future__ import absolute_import, division

import logging
import os
import re

from .utils import nCr
from . import discoverer, mascot, ms_labels


LOGGER = logging.getLogger("pycamv.search")

BACKENDS = {
    ".xml": mascot.read_mascot_xml,
    ".msf": discoverer.read_discoverer_msf,
}

RE_DYN_MODS = re.compile(r"((\d+) )?(.+) \((.+)\)")


class PeptideQuery:
    """
    Attributes
    ----------
    gi : str
    protein : str
    query : int
    filename: str
    # pep_rank : int
    # pep_score : float
    pep_exp_mz : float
    pep_exp_z : int
    pep_seq : str
    pep_var_mods : list of tuple of (int, str, tuple of str)
    pep_fixed_mods : list of tuple of (int, str, tuple of str)
    scan : int
    num_comb : int
    """
    def __init__(
        self, gi, protein, query, filename,
        # pep_rank,
        # pep_score,
        pep_exp_mz, pep_exp_z,
        pep_seq,
        pep_var_mods, pep_fixed_mods, scan,
    ):
        assert _check_mods(pep_var_mods)
        assert _check_mods(pep_fixed_mods)
        self.gi = gi
        self.protein = protein
        self.query = query
        self.filename = filename
        # self.pep_rank = pep_rank
        # self.pep_score = pep_score
        self.pep_exp_mz = pep_exp_mz
        self.pep_exp_z = pep_exp_z
        self.pep_seq = pep_seq
        self.pep_var_mods = pep_var_mods
        self.pep_fixed_mods = pep_fixed_mods
        self.scan = scan
        self.num_comb = self._calc_num_comb()

    def _unique_tuple(self):
        return (
            self.gi,
            self.query,
            self.pep_seq,
            self.scan,
        )

    def __hash__(self):
        return hash(self._unique_tuple())

    def __eq__(self, other):
        if not isinstance(other, PeptideQuery):
            raise TypeError(other)
        return self._unique_tuple() == other._unique_tuple()

    @property
    def pep_mods(self):
        return self.pep_var_mods + self.pep_fixed_mods

    @property
    def get_label_mods(self):
        return [
            mod
            for _, mod, letters in self.pep_mods
            if mod in ms_labels.LABEL_NAMES and "N-term" in letters
        ]

    def _calc_num_comb(self):
        num_comb = 1

        for count, mod, letters in self.pep_var_mods:
            if mod == "Phospho" and letters == ["S", "T"]:
                letters = ["S", "T", "Y"]

            potential_mod_sites = sum(self.pep_seq.count(i) for i in letters)

            # Subtract sites that will be taken up by another modification
            # (i.e. Oxidation and Dioxidation of M)
            for o_count, o_mod, o_letters in self.pep_var_mods:
                if o_letters == letters and o_mod != mod:
                    potential_mod_sites -= o_count

            num_comb *= nCr(potential_mod_sites, count)

        return num_comb


def _check_mods(mods):
    return all(
        isinstance(count, int) and
        isinstance(abbrev, str) and
        isinstance(letters, tuple) and
        all(isinstance(i, str) for i in letters)
        for count, abbrev, letters in mods
    )


def _count_instances(pep_seq, letters):
    return sum(
        (["N-term"] + list(pep_seq) + ["C-term"]).count(letter)
        for letter in letters
    )


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


def read_search_file(path):
    """
    Parse a search input file.

    Parameters
    ----------
    path : str
        Path to search input file.

    Returns
    -------
    fixed_mods : list of str
    var_mods : list of str
    out : list of :class:`PeptideQuery<pycamv.search.PeptideQuery>`
    """
    ext = os.path.splitext(path)[1]
    backend = BACKENDS.get(ext)
    LOGGER.info("Using {} backend for {}".format(backend.__name__, ext))

    return backend(path)