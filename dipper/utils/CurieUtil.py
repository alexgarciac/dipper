__author__ = 'condit@sdsc.edu'

import logging

logger = logging.getLogger(__name__)


class CurieUtil:

    # curie_map should be in the form URI -> prefix:
    # ie: 'http://foo.org/bar_' -> 'bar'
    # this class will not currently handle multiple uris with different prefixes
    def __init__(self, curie_map):
        self.curie_map = curie_map
        self.uri_map = {}
        if curie_map is not None:
            for key, value in curie_map.items():
                self.uri_map[value] = key
        return

    # Get a CURIE from a uri
    def get_curie(self, uri):
        prefix = self.get_curie_prefix(uri)
        if prefix is not None:
            key = self.curie_map[prefix]
            return '%s:%s' % (prefix, uri[len(key):len(uri)])
        return None

    def get_curie_prefix(self, uri):
        for key, value in self.uri_map.items():
            if uri.startswith(key):
                return value
        return None

    # Get a URI from a CURIE
    def get_uri(self, curie):
        if curie is None:
            return None
        parts = curie.split(':')
        if 1 == len(parts):
            if curie != '':
                logger.error("Not a properly formed curie: \"%s\"", curie)
            return None
        prefix = parts[0]
        if prefix in self.curie_map:
            return '%s%s' % (self.curie_map.get(prefix), curie[(curie.index(':') + 1):])
        logger.error("Curie prefix not defined for %s", curie)
        return None
