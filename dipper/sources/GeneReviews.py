import re
import os
import csv
import logging
from bs4 import BeautifulSoup

from dipper.sources.Source import Source, USER_AGENT
from dipper.models.Model import Model
# from dipper.sources.OMIM import OMIM, filter_keep_phenotype_entry_ids
from dipper import config
from dipper.models.Reference import Reference

__author__ = 'nicole'

LOG = logging.getLogger(__name__)
GRDL = 'http://ftp.ncbi.nih.gov/pub/GeneReviews'


class GeneReviews(Source):
    """
    Here we process the GeneReviews mappings to OMIM,
    plus inspect the GeneReviews (html) books to pull the clinical descriptions
    in order to populate the definitions of the terms in the ontology.
    We define the GeneReviews items as classes that are either grouping classes
    over OMIM disease ids (gene ids are filtered out),
    or are made as subclasses of DOID:4 (generic disease).

    Note that GeneReviews
    [copyright policy](http://www.ncbi.nlm.nih.gov/books/NBK138602/)
    (as of 2015.11.20) says:

    GeneReviews® chapters are owned by the University of Washington, Seattle,
    © 1993-2015. Permission is hereby granted to reproduce, distribute,
    and translate copies of content materials provided that
    (i) credit for source (www.ncbi.nlm.nih.gov/books/NBK1116/)
    and copyright (University of Washington, Seattle)
    are included with each copy;
    (ii) a link to the original material is provided whenever the material is
    published elsewhere on the Web; and
    (iii) reproducers, distributors, and/or translators comply with this
    copyright notice and the GeneReviews Usage Disclaimer.

    This script doesn't pull the GeneReviews books from the NCBI Bookshelf
    directly; scripting this task is expressly prohibited by
    [NCBIBookshelf policy](http://www.ncbi.nlm.nih.gov/books/NBK45311/).
    However, assuming you have acquired the books (in html format) via
    permissible means, a parser for those books is provided here to extract
    the clinical descriptions to define the NBK identified classes.

    """

    OMIMURL = 'https://data.omim.org/downloads/'
    OMIMFTP = OMIMURL + config.get_config()['keys']['omim']

    files = {
        'idmap': {
            'file': 'NBKid_shortname_OMIM.txt',
            'url': GRDL + '/NBKid_shortname_OMIM.txt'
        },
        'titles': {
            'file': 'GRtitle_shortname_NBKid.txt',
            'url': GRDL + '/GRtitle_shortname_NBKid.txt'
        },
        'mimtitles': {
            'file': 'mimTitles.txt',
            'url':  OMIMFTP + '/mimTitles.txt',
            'headers': {'User-Agent': USER_AGENT},
            'clean': OMIMURL,
            'columns': (  # expected
                'Prefix',
                'Mim Number',
                'Preferred Title; symbol',
                'Alternative Title(s); symbol(s)',
                'Included Title(s); symbols',
            ),
        },
    }

    def __init__(self, graph_type, are_bnodes_skolemized):
        super().__init__(
            graph_type,
            are_bnodes_skolemized,
            'genereviews',
            ingest_title='Gene Reviews',
            ingest_url='http://genereviews.org/',
            license_url=None,
            data_rights='http://www.ncbi.nlm.nih.gov/books/NBK138602/',
            # file_handle=None
        )

        self.dataset.set_citation('GeneReviews:NBK1116')

        self.book_ids = set()
        self.all_books = {}

        if 'test_ids' not in config.get_config() or\
                'disease' not in config.get_config()['test_ids']:
            LOG.warning("not configured with disease test ids.")
            self.test_ids = list()
        else:
            # select ony those test ids that are omim's.
            self.test_ids = config.get_config()['test_ids']['disease']

        self.omim_replaced = {}  # id_num to SET of id nums
        self.omim_type = {}      # id_num to onto_term

        return

    def fetch(self, is_dl_forced=False):
        """
        We fetch GeneReviews id-label map and id-omim mapping files from NCBI.
        :return: None
        """
        self.get_files(is_dl_forced)

        # load and tag a list of OMIM IDs with types
        self.omim_type = self.find_omim_type()

        return

    def parse(self, limit=None):
        """
        :return: None
        """

        if self.testOnly:
            self.testMode = True

        self._get_titles(limit)
        self._get_equivids(limit)

        self.create_books()
        self.process_nbk_html(limit)

        # no test subset for now; test == full graph
        self.testgraph = self.graph
        return

    def _get_equivids(self, limit):
        """
        The file processed here is of the format:
        #NBK_id GR_shortname    OMIM
        NBK1103 trimethylaminuria       136132
        NBK1103 trimethylaminuria       602079
        NBK1104 cdls    122470
        Where each of the rows represents a mapping between
        a gr id and an omim id. These are a 1:many relationship,
        and some of the omim ids are genes(not diseases).
        Therefore, we need to create a loose coupling here.
        We make the assumption that these NBKs are generally higher-level
        grouping classes; therefore the OMIM ids are treated as subclasses.

        (This assumption is poor for those omims that are actually genes,
        but we have no way of knowing what those are here...
        we will just have to deal with that for now.)    -- fixed

        :param limit:
        :return:

        """
        raw = '/'.join((self.rawdir, self.files['idmap']['file']))
        model = Model(self.graph)
        line_counter = 0

        # we look some stuff up in OMIM, so initialize here
        # omim = OMIM(self.graph_type, self.are_bnodes_skized)
        id_map = {}
        allomimids = set()
        with open(raw, 'r', encoding="utf8") as csvfile:
            filereader = csv.reader(csvfile, delimiter='\t', quotechar='\"')
            for row in filereader:
                line_counter += 1
                if line_counter == 1:  # skip header
                    continue
                (nbk_num, shortname, omim_num) = row
                gr_id = 'GeneReviews:'+nbk_num
                omim_id = 'OMIM:' + omim_num
                if not (
                        (self.testMode and
                         len(self.test_ids) > 0 and
                         omim_id in self.test_ids) or not
                        self.testMode):
                    continue

                # sometimes there's bad omim nums
                omim_num = omim_num.strip()
                if len(omim_num) > 6:
                    LOG.warning(
                        "OMIM number incorrectly formatted in row %d; skipping:\n%s",
                        line_counter, '\t'.join(row))
                    continue

                # build up a hashmap of the mappings; then process later
                if nbk_num not in id_map:
                    id_map[nbk_num] = set()
                id_map[nbk_num].add(omim_num)

                # add the class along with the shortname
                model.addClassToGraph(gr_id, None)
                model.addSynonym(gr_id, shortname)

                allomimids.add(omim_num)

                if not self.testMode and limit is not None and line_counter > limit:
                    break

            # end looping through file

        # get the omim ids that are not genes
        # entries_that_are_phenotypes = omim.process_entries(
        #    list(allomimids), filter_keep_phenotype_entry_ids, None, None,
        #    limit=limit, globaltt=self.globaltt)
        #
        # LOG.info(
        #    "Filtered out %d/%d entries that are genes or features",
        #    len(allomimids)-len(entries_that_are_phenotypes), len(allomimids))
        ##########################################################################

        # given all_omim_ids from GR,
        # we want to update any which are changed or removed
        # before deciding which are disease / phenotypes
        replaced = allomimids & self.omim_replaced.keys()
        if replaced is not None and len(replaced) > 0:
            LOG.warning("These OMIM ID's are past their pull date: %s", str(replaced))
            for oid in replaced:
                allomimids.remove(oid)
                replacements = self.omim_replaced[oid]
                for rep in replacements:
                    allomimids.update(rep)
        # guard against omim identifiers which have been removed
        obsolete = [
            o for o in self.omim_type
            if self.omim_type[o] == self.globaltt['obsolete']]
        removed = allomimids & set(obsolete)
        if removed is not None and len(removed) > 0:
            LOG.warning("These OMIM ID's are gone: %s", str(removed))
            for oid in removed:
                allomimids.remove(oid)
        # filter for disease /phenotype types (we can argue about what is included)
        omim_phenotypes = set([
            omim for omim in self.omim_type if self.omim_type[omim] in (
                self.globaltt['Phenotype'],
                self.globaltt['has_affected_feature'],  # both a gene and a phenotype
                self.globaltt['heritable_phenotypic_marker'])])  # probable phenotype
        LOG.info(
            "Have %i omim_ids globally typed as phenotypes from OMIM",
            len(omim_phenotypes))

        entries_that_are_phenotypes = allomimids & omim_phenotypes
        LOG.info(
            "Filtered out %d/%d entries that are genes or features",
            len(allomimids - entries_that_are_phenotypes), len(allomimids))

        for nbk_num in self.book_ids:
            gr_id = 'GeneReviews:'+nbk_num
            if nbk_num in id_map:
                omim_ids = id_map.get(nbk_num)
                for omim_num in omim_ids:
                    omim_id = 'OMIM:'+omim_num
                    # add the gene reviews as a superclass to the omim id,
                    # but only if the omim id is not a gene
                    if omim_id in entries_that_are_phenotypes:
                        model.addClassToGraph(omim_id, None)
                        model.addSubClass(omim_id, gr_id)
            # add this as a generic subclass of DOID:4
            model.addSubClass(gr_id, 'DOID:4')

        return

    def _get_titles(self, limit):
        """
        The file processed here is of the format:
        #NBK_id GR_shortname    OMIM
        NBK1103 trimethylaminuria       136132
        NBK1103 trimethylaminuria       602079
        NBK1104 cdls    122470
        Where each of the rows represents a mapping between
        a gr id and an omim id. These are a 1:many relationship,
        and some of the omim ids are genes (not diseases).
        Therefore, we need to create a loose coupling here.
        We make the assumption that these NBKs are generally higher-level
        grouping classes; therefore the OMIM ids are treated as subclasses.
        (This assumption is poor for those omims that are actually genes,
        but we have no way of knowing what those are here...
        we will just have to deal with that for now.)
        :param limit:
        :return:
        """
        raw = '/'.join((self.rawdir, self.files['titles']['file']))
        model = Model(self.graph)
        line_counter = 0
        with open(raw, 'r', encoding='latin-1') as csvfile:
            filereader = csv.reader(csvfile, delimiter='\t', quotechar='\"')
            header = next(filereader)
            line_counter = 1
            colcount = len(header)
            if colcount != 4:  # ('GR_shortname', 'GR_Title', 'NBK_id', 'PMID')
                LOG.error("Unexpected Header %s", header)
                exit(-1)
            for row in filereader:
                line_counter += 1
                if len(row) != colcount:
                    LOG.error("Unexpected row. got: %s", row)
                    LOG.error("Expected data for: %s", header)
                    exit(-1)
                (shortname, title, nbk_num, pmid) = row
                gr_id = 'GeneReviews:'+nbk_num

                self.book_ids.add(nbk_num)  # a global set of the book nums

                if limit is None or line_counter < limit:
                    model.addClassToGraph(gr_id, title)
                    model.addSynonym(gr_id, shortname)
                # TODO include the new PMID?

        return

    def create_books(self):

        # note that although we put in the url to the book,
        # NCBI Bookshelf does not allow robots to download content
        book_item = {
            'file': 'books/',
            'url': ''
        }

        for nbk in self.book_ids:
            b = book_item.copy()
            b['file'] = '/'.join(('books', nbk+'.html'))
            b['url'] = 'http://www.ncbi.nlm.nih.gov/books/'+nbk
            self.all_books[nbk] = b

        return

    def process_nbk_html(self, limit):
        """
        Here we process the gene reviews books to fetch
        the clinical descriptions to include in the ontology.
        We only use books that have been acquired manually,
        as NCBI Bookshelf does not permit automated downloads.
        This parser will only process the books that are found in
        the ```raw/genereviews/books``` directory,
        permitting partial completion.

        :param limit:
        :return:
        """
        model = Model(self.graph)
        c = 0
        books_not_found = set()
        for nbk in self.book_ids:
            c += 1
            nbk_id = 'GeneReviews:'+nbk
            book_item = self.all_books.get(nbk)
            url = '/'.join((self.rawdir, book_item['file']))

            # figure out if the book is there; if so, process, otherwise skip
            book_dir = '/'.join((self.rawdir, 'books'))
            book_files = os.listdir(book_dir)
            if ''.join((nbk, '.html')) not in book_files:
                # LOG.warning("No book found locally for %s; skipping", nbk)
                books_not_found.add(nbk)
                continue
            LOG.info("Processing %s", nbk)

            page = open(url)
            soup = BeautifulSoup(page.read())

            # sec0 == clinical description
            clin_summary = \
                soup.find(
                    'div', id=re.compile(".*Summary.sec0"))
            if clin_summary is not None:
                p = clin_summary.find('p')
                ptext = p.text
                ptext = re.sub(r'\s+', ' ', ptext)

                ul = clin_summary.find('ul')
                if ul is not None:
                    item_text = list()
                    for li in ul.find_all('li'):
                        item_text.append(re.sub(r'\s+', ' ', li.text))
                    ptext += ' '.join(item_text)

                # add in the copyright and citation info to description
                ptext = ' '.join((
                    ptext, '[GeneReviews:NBK1116, GeneReviews:NBK138602, ' +
                    nbk_id + ']'))

                model.addDefinition(nbk_id, ptext.strip())

            # get the pubs
            pmid_set = set()
            pub_div = soup.find('div', id=re.compile(r".*Literature_Cited"))
            if pub_div is not None:
                ref_list = pub_div.find_all('div', attrs={'class': "bk_ref"})
                for r in ref_list:
                    for a in r.find_all(
                            'a', attrs={'href': re.compile(r"pubmed")}):
                        if re.match(r'PubMed:', a.text):
                            pmnum = re.sub(r'PubMed:\s*', '', a.text)
                        else:
                            pmnum = re.search(r'\/pubmed\/(\d+)$', a['href']).group(1)
                        if pmnum is not None:
                            pmid = 'PMID:'+str(pmnum)
                            self.graph.addTriple(
                                pmid, self.globaltt['is_about'], nbk_id)
                            pmid_set.add(pmnum)
                            reference = Reference(
                                self.graph, pmid, self.globaltt['journal article'])
                            reference.addRefToGraph()

            # TODO add author history, copyright, license to dataset

            # TODO get PMID-NBKID equivalence (near foot of page),
            # and make it "is about" link
            # self.gu.addTriple(
            #   self.graph, pmid,
            #   self.globaltt['is_about'], nbk_id)
            # for example: NBK1191 PMID:20301370

            # add the book to the dataset
            self.dataset.setFileAccessUrl(book_item['url'])

            if limit is not None and c > limit:
                break

            # finish looping through books

        l = len(books_not_found)
        if len(books_not_found) > 0:
            if l > 100:
                LOG.warning("There were %d books not found.", l)
            else:
                LOG.warning(
                    "The following %d books were not found locally: %s", l,
                    str(books_not_found))
        LOG.info("Finished processing %d books for clinical descriptions", c-l)

        return

    def find_omim_type(self):
        '''
        This f(x) needs to be rehomed and shared.
        Use OMIM's discription of their identifiers
        to heuristically partition them into genes | phenotypes-diseases
        type could be
            - `obsolete`  Check `omim_replaced`  populated as side effect
            - 'Suspected' (phenotype)  Ignoring thus far
            - 'gene'
            - 'Phenotype'
            - 'heritable_phenotypic_marker'   Probable phenotype
            - 'has_affected_feature'  Use as both a gene and a phenotype

        :return hash of omim_number to ontology_curie
        '''
        myfile = '/'.join((self.rawdir, self.files['mimtitles']['file']))
        omim_type = {}
        line_counter = 1
        with open(myfile, 'r') as fh:
            reader = csv.reader(fh, delimiter='\t')
            for row in reader:
                line_counter += 1
                if row[0][0] == '#':     # skip comments
                    continue
                elif row[0] == 'Caret':  # moved|removed|split -> moved twice
                    # populating a dict from an omim to a set of omims
                    # here as a side effect which is less than ideal
                    (prefix, omim_id, destination, empty, empty) = row
                    omim_type[omim_id] = self.globaltt['obsolete']
                    if row[2][:9] == 'MOVED TO ':
                        token = row[2].split(' ')
                        rep = token[2]
                        if not re.match(r'^[0-9]{6}$', rep):
                            LOG.error('Report malformed omim replacement %s', rep)
                            # clean up ones I know about
                            if rep[0] == '{' and rep[7] == '}':
                                rep = rep[1:6]
                            if len(rep) == 7 and rep[6] == ',':
                                rep = rep[:5]    
                        # asuming splits are typically to both gene & phenotype
                        if len(token) > 3:
                            self.omim_replaced[omim_id] = {rep, token[4]}
                        else:
                            self.omim_replaced[omim_id] = {rep}

                elif row[0] == 'Asterisk':  # declared as gene
                    (prefix, omim_id, pref_label, alt_label, inc_label) = row
                    omim_type[omim_id] = self.globaltt['gene']
                elif row[0] == 'NULL':
                    #  potential model of disease?
                    (prefix, omim_id, pref_label, alt_label, inc_label) = row
                    #
                    omim_type[omim_id] = self.globaltt['Suspected']   # NCIT:C71458
                elif row[0] == 'Number Sign':
                    (prefix, omim_id, pref_label, alt_label, inc_label) = row
                    omim_type[omim_id] = self.globaltt['Phenotype']
                elif row[0] == 'Percent':
                    (prefix, omim_id, pref_label, alt_label, inc_label) = row
                    omim_type[omim_id] = self.globaltt['heritable_phenotypic_marker']
                elif row[0] == 'Plus':
                    (prefix, omim_id, pref_label, alt_label, inc_label) = row
                    # to be interperted as  a gene and/or a phenotype
                    omim_type[omim_id] = self.globaltt['has_affected_feature']
                else:
                    LOG.error('Unlnown OMIM type line ')
        return omim_type

    def getTestSuite(self):
        import unittest
        from tests.test_genereviews import GeneReviewsTestCase

        test_suite = unittest.TestLoader().loadTestsFromTestCase(GeneReviewsTestCase)

        return test_suite
