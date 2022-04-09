#!/usr/bin/env python3

# ----------------------------------------------------------------------------
# Copyright (c) 2020--, Qiyun Zhu.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file LICENSE, distributed with this software.
# ----------------------------------------------------------------------------

"""Functions for matching reads and genes using an ordinal system.
"""

from collections import defaultdict
from itertools import chain

from .align import infer_align_format, assign_parser


def ordinal_mapper(fh, coords, idmap, fmt=None, n=1000000, th=0.8,
                   prefix=False):
    """Read an alignment file and match reads and genes in an ordinal system.

    Parameters
    ----------
    fh : file handle
        Alignment file to parse.
    coords : dict of list
        Gene coordinates table.
    idmap : dict of list
        Gene identifiers.
    fmt : str, optional
        Alignment file format.
    n : int, optional
        Number of lines per chunk.
    th : float
        Minimum threshold of overlap length : alignment length for a match.
    prefix : bool
        Prefix gene IDs with nucleotide IDs.

    See Also
    --------
    align.plain_mapper

    Yields
    ------
    tuple of str
        Query queue.
    dict of set of str
        Subject(s) queue.
    """
    # determine file format
    fmt, head = (fmt, []) if fmt else infer_align_format(fh)

    # assign parser for given format
    parser = assign_parser(fmt)

    # cached list of query Ids for reverse look-up
    # gene Ids are unique, but read Ids can have duplicates (i.e., one read is
    # mapped to multiple loci on a genome), therefore an incremental integer
    # here replaces the original read Id as its identifer
    rids = []
    rid_append = rids.append

    # cached map of read to coordinates
    locmap = defaultdict(list)

    def flush():
        """Match reads in current chunk with genes from all nucleotides.

        Returns
        -------
        tuple of str
            Query queue.
        dict of set of str
            Subject(s) queue.
        """
        # master read-to-gene(s) map
        res = defaultdict(set)

        with open('stats.txt', 'a') as fx:
            nums = [str(len(x) / 2) for x in locmap.values()]
            print(','.join(nums), file=fx)

        for nucl, locs in locmap.items():

            # it's possible that no gene was annotated on the nucleotide
            try:
                glocs = coords[nucl]
            except KeyError:
                continue

            # get reference to gene identifiers
            gids = idmap[nucl]

            # append prefix if needed
            pfx = nucl + '_' if prefix else ''

            # execute ordinal algorithm when reads are many
            if len(locs) > 8:

                # merge and sort coordinates
                # question is to add unsorted read coordinates into pre-sorted
                # gene coordinates
                # Python's Timsort algorithm is efficient for this task
                queue = sorted(chain(glocs, locs))

                # map reads to genes using the core algorithm
                for read, gene in match_read_gene(queue):

                    # add read-gene pairs to the master map
                    res[rids[read]].add(pfx + gids[gene])

            # execute naive algorithm when reads are few
            else:
                for read, gene in match_read_gene_naive(glocs, locs):
                    res[rids[read]].add(pfx + gids[gene])

        # return matching read Ids and gene Ids
        return res.keys(), res.values()

    this = None  # current query Id
    target = n   # target line number at end of current chunk

    # parse alignment file
    for i, line in enumerate(chain(iter(head), fh)):

        # parse current alignment line
        try:
            query, subject, _, length, beg, end = parser(line)[:6]
        except (TypeError, IndexError):
            continue

        # skip if length is not available
        if not length:
            continue

        # when query Id changes and chunk limits has been reached
        if query != this and i >= target:

            # flush: match currently cached reads with genes and yield
            yield flush()

            # re-initiate read Ids, length map and location map
            rids = []
            rid_append = rids.append
            locmap = defaultdict(list)

            # next target line number
            target = i + n

        # append read Id, alignment length and location
        idx = len(rids)
        rid_append(query)

        # -int(-x // 1) is equivalent to math.ceil(x) but faster
        locmap[subject].extend((
            (beg << 48) + (-int(-length * th // 1) << 31) + idx,
            (end << 48) + idx))
        this = query

    # final flush
    yield flush()


def ordinal_parser_dummy(fh, parser):
    """Alignment parsing functionalities stripped from for `ordinal_mapper`.

    Parameters
    ----------
    fh : file handle
        Alignment file to parse.
    parser : callable
        Function to parse alignment lines of certain format.

    Returns
    -------
    list of str
        Read Ids in same order as in alignment file.
    defaultdict of dict of int
        Map of read indices to alignment lengths per nucleotide.
    defaultdict of list of (int, bool, bool, str)
        Flattened list of read coordinates per nucleotide.

    See Also
    --------
    ordinal_mapper
    match_read_gene_dummy
    .tests.test_ordinal.OrdinalTests.test_ordinal_parser_dummy

    Notes
    -----
    This is a dummy function only for test and demonstration purpose but not
    called anywhere in the program. See its unit test for details.
    """
    rids = []
    lenmap = defaultdict(dict)
    locmap = defaultdict(list)

    for line in fh:
        try:
            query, subject, _, length, start, end = parser(line)[:6]
        except (TypeError, IndexError):
            continue
        idx = len(rids)
        rids.append(query)
        lenmap[subject][idx] = length
        locmap[subject].extend((
            (start, True, False, idx),
            (end,  False, False, idx)))

    return rids, lenmap, locmap


def load_gene_coords(fh, sort=False):
    """Read coordinates of genes on genomes.

    Parameters
    ----------
    fh : file handle
        Gene coordinates file.
    sort : bool, optional
        Whether sort gene coordinates.

    Returns
    -------
    dict of int
        Binarized gene coordinate information per nucleotide.
    dict of list of str
        Gene IDs.
    bool
        Whether there are duplicate gene IDs.

    See Also
    --------
    match_read_gene

    Notes
    -----
    This data structure is central to this algorithm. Starting and ending
    coordinates of each gene are separated and flattened into a sorted list.
    which enables only one round of list traversal for the entire set of genes
    plus reads.

    See the docstring of `match_read_gene` for details.
    """
    coords = {}
    queue_extend = None

    idmap = {}
    gids = None
    gids_append = None

    isdup = None
    used = set()
    used_add = used.add

    for line in fh:

        # ">" or "#" indicates genome (nucleotide) name
        c0 = line[0]
        if c0 in '>#':

            # double ">" or "#" indicates genome name, which serves as
            # a super group of subsequent nucleotide names; to be ignored
            if line[1] != c0:
                nucl = line[1:].strip()
                coords[nucl] = []
                queue_extend = coords[nucl].extend
                gids = idmap[nucl] = []
                gids_append = gids.append
        else:
            x = line.rstrip().split('\t')

            # begin and end positions are based on genome (nucleotide)
            try:
                beg, end = sorted((int(x[1]), int(x[2])))
            except (IndexError, ValueError):
                raise ValueError(
                    f'Cannot extract coordinates from line: "{line}".')
            idx = len(gids)
            gene = x[0]
            gids_append(gene)
            queue_extend(((beg << 48) + (3 << 30) + idx,
                          (end << 48) + (1 << 30) + idx))

            # check duplicate
            if isdup is None:
                if gene in used:
                    isdup = True
                else:
                    used_add(gene)

    # sort gene coordinates per nucleotide
    if sort:
        for queue in coords.values():
            queue.sort()

    return coords, idmap, isdup or False


def match_read_gene(queue):
    """Associate reads with genes based on a sorted queue of coordinates.

    Parameters
    ----------
    queue : list of int
        Sorted queue of coordinates.

    Yields
    ------
    int
        Read index.
    int
        Gene index.

    See Also
    --------
    load_gene_coords
    match_read_gene_dummy
    .tests.test_ordinal.OrdinalTests.test_match_read_gene_dummy

    Notes
    -----
    This algorithm is the core of this module. It uses a flattened, sorted
    list to store starting and ending coordinates of both genes and reads.
    Only one round of traversal (O(n)) of this list is needed to accurately
    find all gene-read matches.

    Refer to its unit test `test_match_read_gene` for an illustrated example.

    This function is the most compute-intensive step in the entire analysis,
    therefore it has been deeply optimized to increase performance wherever
    possible. Notably, it extensively uses bitwise operations to extract
    multiple pieces of information from a single integer.

    Specifically, each coordinate (an integer) has the following information
    (from right to left):

    - Bits  1-30: Index of gene / read (30 bits, max.: 1,073,741,823).
    - Bits    31: Whether it is a gene (1) or a read (0) (1 bit).
    - Bits 32-58: Whether it is the start (positive) or end (0) of a gene /
                  read. If start, the value represents the effective length of
                  an alignment if it's a read, or 1 if it's a gene (17 bits,
                  max.: 131,071).
    - Bits 59-  : Coordinate (position on the genome, nt) (unlimited)

    The Python code to extract these pieces of information is:

    - Coordinate:       `code >> 48`
    - Effective length: `code >> 31 & (1 << 17) - 1`, or `code >> 31 & 131071`
    - Gene or read:     `code & (1 << 30)`
    - Gene/read index:  `code & (1 << 30) - 1`

    Note: Repeated bitwise operations are usually more efficient that a single
    bitwise operation assigned to a new variable.
    """
    genes = {}  # current genes cache
    reads = {}  # current reads cache

    # cache method references
    genes_items = genes.items
    reads_items = reads.items

    genes_pop = genes.pop
    reads_pop = reads.pop

    # walk through flattened queue of reads and genes
    for code in queue:

        # if this is a gene,
        if code & (1 << 30):

            # when a gene begins,
            # if code >> 31 & 131071:
            if code & (1 << 31):

                # add it to cache
                genes[code & (1 << 30) - 1] = code >> 48

            # when a gene ends,
            else:

                # find gene start
                gloc = genes_pop(code & (1 << 30) - 1)

                # check cached reads for matches
                for rid, rloc in reads_items():

                    # is a match if read/gene overlap is long enough
                    if (code >> 48) - max(gloc, rloc >> 17) >= rloc & 131071:
                        yield rid, code & (1 << 30) - 1

        # if this is a read,
        else:

            # when a read begins,
            if code >> 31 & 131071:

                # add it and its effective length to cache
                reads[code & (1 << 30) - 1] = (code >> 31) - 1

            # when a read ends,
            else:

                # find read start and effective length
                rloc = reads_pop(code & (1 << 30) - 1)

                # check cached genes
                for gid, gloc in genes_items():

                    # same as above
                    if (code >> 48) - max(gloc, rloc >> 17) >= rloc & 131071:
                        yield code & (1 << 30) - 1, gid


def match_read_gene_naive(geneque, readque):
    """Associate reads with genes using a native approach, which performs
    nested iteration over genes and reads.

    Parameters
    ----------
    geneque : list of int
        Sorted queue of genes.
    readque : list of int
        Paired queue of reads.

    Yields
    ------
    int
        Read index.
    int
        Gene index.

    See Also
    --------
    match_read_gene
    """
    # only genes are to be cached
    genes = {}
    genes_pop = genes.pop

    # pre-calculate id, start, end, effective length of reads
    it = iter(readque)
    reads = [(s & (1 << 30) - 1,
              s >> 48,
              e >> 48,
              s >> 31 & 131071) for s, e in zip(it, it)]

    # iterate over gene queue
    for code in geneque:
        if code & (1 << 31):
            genes[code & (1 << 30) - 1] = code >> 48
        else:
            gid = code & (1 << 30) - 1
            gs, ge = genes_pop(gid), code >> 48

            # check reads for matches
            for rid, rs, re, L in reads:
                if min(ge, re) - max(gs, rs) >= L:
                    yield rid, gid


def match_read_gene_g1(queue):
    """Associate reads with genes based on a sorted queue of coordinates,
    assuming genes are not overlapped.

    Parameters
    ----------
    queue : list of int
        Sorted queue of coordinates.

    Yields
    ------
    int
        Read index.
    int
        Gene index.

    See Also
    --------
    match_read_gene

    Notes
    -----
    The difference from `match_read_gene` is that the gene cache is a scalar
    not a dict, and it can store only one gene.
    """
    gene, reads = None, {}
    reads_items, reads_pop = reads.items, reads.pop
    for code in queue:
        if code & (1 << 30):
            if code & (1 << 31):
                gene = code
            else:
                gloc, gene = gene >> 48, None
                for rid, rloc in reads_items():
                    if (code >> 48) - max(gloc, rloc >> 17) >= rloc & 131071:
                        yield rid, code & (1 << 30) - 1
        else:
            if code >> 31 & 131071:
                reads[code & (1 << 30) - 1] = (code >> 31) - 1
            else:
                rloc = reads_pop(code & (1 << 30) - 1)
                if gene and (code >> 48) - max(gene >> 48,
                                               rloc >> 17) >= rloc & 131071:
                    yield code & (1 << 30) - 1, gene & (1 << 30) - 1


def match_read_gene_dummy(queue, lens, th):
    """Associate reads with genes based on a sorted queue of coordinates.
    Parameters
    ----------
    queue : list of tuple of (int, bool, bool, int)
        Sorted queue of coordinates (location, start or end, gene or read,
        index).
    lens : dict
        Read-to-alignment length map.
    th : float
        Threshold for read/gene overlapping fraction.

    Yields
    ------
    int
        Read index.
    int
        Gene index.

    See Also
    --------
    match_read_gene
    .tests.test_ordinal.OrdinalTests.test_match_read_gene_dummy

    Notes
    -----
    This is a dummy function which is only for test and demonstration purpose,
    but is not called anywhere in the program.

    The formal function `match_read_gene` extensively uses bitwise operations
    and thus is hard to read. Therefore the current function, which represents
    the original form of prior to optimization, is retained.
    """
    genes = {}  # current genes
    reads = {}  # current reads

    # walk through flattened queue of reads and genes
    for loc, is_start, is_gene, idx in queue:
        if is_gene:

            # when a gene starts, added to gene cache
            if is_start:
                genes[idx] = loc

            # when a gene ends,
            else:

                # find gene start and remove it from cache
                gloc = genes.pop(idx)

                # check cached reads for matches
                for rid, rloc in reads.items():

                    # is a match if read/gene overlap is long enough
                    if loc - max(gloc, rloc) + 1 >= lens[rid] * th:
                        yield rid, idx

        # the same for reads
        else:
            if is_start:
                reads[idx] = loc
            else:
                rloc = reads.pop(idx)
                for gid, gloc in genes.items():
                    if loc - max(rloc, gloc) + 1 >= lens[idx] * th:
                        yield idx, gid


def calc_gene_lens(mapper):
    """Calculate gene lengths by start and end coordinates.

    Parameters
    ----------
    mapper : callable
        Ordinal mapper.

    Returns
    -------
    dict of int
        Mapping of genes to lengths.
    """
    res = {}
    prefix = mapper.keywords['prefix']
    idmap = mapper.keywords['idmap']
    for nucl, queue in mapper.keywords['coords'].items():
        idmap_ = idmap[nucl]
        nucl += '_'
        for code in queue:
            gid = idmap_[code & (1 << 30) - 1]
            if prefix:
                gid = nucl + gid
            if code >> 31 & 131071:
                res[gid] = 1 - (code >> 48)
            else:
                res[gid] += code >> 48
    return res
