"""
This module provides functionality for interacting with mass spec scan data.
"""

from __future__ import absolute_import, division

import logging
import os
import tempfile
import xml.etree.ElementTree as ET

from . import ms_labels, proteowizard, regexes


LOGGER = logging.getLogger("pycamv.scans")


class ScanQuery:
    """
    Attributes
    ----------
    scan : int
    isolation_mz : float or None
    window_offset : tuple of (int, int) or None
    precursor_scan : int or None
    collision_type : str or None
    c13_num : int
    """
    def __init__(
        self, scan,
        isolation_mz=None, window_offset=None, precursor_scan=None,
        collision_type=None, c13_num=0,
    ):
        self.scan = scan
        self.precursor_scan = precursor_scan
        self.window_offset = window_offset
        self.isolation_mz = isolation_mz
        self.collision_type = collision_type
        self.c13_num = c13_num


def _scanquery_from_spectrum(pep_query, spectrum):
    """
    Parameters
    ----------
    pep_query : :class:`PeptideQuery<pycamv.pep_query.PeptideQuery>`
    spectrum : :class:`pymzml.spec.Spectrum<spec.Spectrum>`

    Returns
    -------
    :class:`ScanQuery<pycamv.scans.ScanQuery>`
    """
    # prefix = {"mzml": "http://psi.hupo.org/ms/mzml"}

    scan = spectrum["id"]
    isolation_mz = spectrum["isolation window target m/z"]
    window_offset = (
        spectrum["isolation window lower offset"],
        spectrum["isolation window upper offset"],
    )
    collision_type = regexes.RE_COLLISION_TYPE.search(
        spectrum["filter string"]
    ).group(1).upper()

    ns = {"ns0": "http://psi.hupo.org/ms/mzml"}
    precursor = spectrum.xmlTreeIterFree.find(
        "ns0:precursorList/ns0:precursor",
        ns,
    ) or spectrum.xmlTreeIterFree.find(
        "precursorList/precursor",
    )

    if precursor is None:
        LOGGER.error(
            "Unable to find precursor scan info in scan {}".format(scan)
        )
        LOGGER.error(
            ET.tostring(
                spectrum.xmlTreeIterFree,
                encoding='utf8', method='xml',
            )
        )

    spectrum_ref = precursor.get("spectrumRef")

    precursor_scan = int(
        regexes.RE_PRECURSOR_SCAN.search(spectrum_ref).group(1)
    )

    return ScanQuery(
        scan,
        precursor_scan=precursor_scan,
        window_offset=window_offset,
        isolation_mz=isolation_mz,
        collision_type=collision_type,
        c13_num=_c13_num(pep_query, isolation_mz)
    )


def _c13_num(pep_query, isolation_mz):
    """
    Counts the number of C13 atoms in a query, based on the mass-error between
    the expected and isolated m/z values.

    Parameters
    ----------
    pep_query : :class:`PeptideQuery<pycamv.pep_query.PeptideQuery>`
    isolation_mz : float

    Returns
    -------
    int
    """
    return int(
        round(
            pep_query.pep_exp_z *
            abs(pep_query.pep_exp_mz - isolation_mz)
        )
    )


def get_precursor_peak_window(scan_queries, ms_data, window_size=1):
    """
    Get ion peaks around each peptide's precursor m/z range.

    Parameters
    ----------
    pep_queries : list of :class:`PeptideQuery<pycamv.pep_query.PeptideQuery>`
    ms2_data : :class:`pymzml.run.Reader<run.Reader>`

    Returns
    -------
    list of list of tuple of (float, float)
    """
    def _get_percursor_peaks(query):
        window = (
            query.isolation_mz - window_size,
            query.isolation_mz + window_size,
        )

        return [
            (mz, i)
            for mz, i in ms_data[query.precursor_scan].centroidedPeaks
            if mz > window[0] and mz < window[1]
        ]

    return [
        _get_percursor_peaks(query)
        for query in scan_queries
    ]


def get_label_peak_window(pep_queries, ms2_data, window_size=1):
    """
    Get ion peaks around each peptide's label m/z range.

    Parameters
    ----------
    pep_queries : list of :class:`PeptideQuery<pycamv.pep_query.PeptideQuery>`
    ms2_data : :class:`pymzml.run.Reader<run.Reader>`

    Returns
    -------
    list of list of tuple of (float, float)
    """

    def _get_labels_peaks(query):
        label_mods = query.get_label_mods

        if not label_mods:
            return []

        window = ms_labels.LABEL_MZ_WINDOW[label_mods[0]]
        window = (
            window[0] - window_size,
            window[1] + window_size
        )

        return [
            (mz, i)
            for mz, i in ms2_data[query.scan].centroidedPeaks
            if mz > window[0] and mz < window[1]
        ]

    return [
        _get_labels_peaks(pep_query)
        for pep_query in pep_queries
    ]


def get_scan_data(raw_path, pep_queries, out_dir=None):
    """
    Gets MS^2 and MS data for all scans in queries.

    Parameters
    ----------
    raw_path : str
    pep_queries : list of :class:`PeptideQuery<pycamv.pep_query.PeptideQuery>`
    out_dir : str, optional

    Returns
    -------
    scan_queries : list of :class:`ScanQuery<pycamv.scans.ScanQuery>`
    ms2_data : :class:`pymzml.run.Reader<run.Reader>`
    ms_data : :class:`pymzml.run.Reader<run.Reader>`
    """
    if out_dir is None:
        out_dir = tempfile.mkdtemp()

    # Collect MS^2 data
    LOGGER.info("Converting MS^2 data.")
    ms2_data = proteowizard.raw_to_mzml(
        raw_path, os.path.join(out_dir, "ms2"),
        scans=sorted(set(pep_query.scan for pep_query in pep_queries)),
    )

    # Build a list of scan queries, including data about each scan
    scan_queries = [
        _scanquery_from_spectrum(pep_query, ms2_data[pep_query.scan])
        for pep_query in pep_queries
    ]

    # Collect MS^1 data
    LOGGER.info("Converting MS^1 data.")
    ms_data = proteowizard.raw_to_mzml(
        raw_path, os.path.join(out_dir, "ms"),
        scans=sorted(set(i.precursor_scan for i in scan_queries)),
    )

    return scan_queries, ms2_data, ms_data
