from dipper.models.assoc.Association import Assoc
from dipper.models.Family import Family


__author__ = 'nlw'


class OrthologyAssoc(Assoc):

    def __init__(self, graph, definedby, gene1, gene2, rel=None):
        super().__init__(graph, definedby)
        self.globaltt = graph.globaltt
        self.globaltcid = graph.globaltcid
        self.curie_map = graph.curie_map

        if rel is None:
            rel = self.globaltt['in orthology relationship with']  # default

        self.set_subject(gene1)
        self.set_object(gene2)
        self.set_relationship(rel)
        return

    def add_gene_family_to_graph(self, family_id):
        """
        Make an association between a group of genes and some grouping class.
        We make the assumption that the genes in the association
        are part of the supplied family_id, and that the genes have
        already been declared as classes elsewhere.
        The family_id is added as an individual of type DATA:gene_family.

        Triples:
        <family_id> a EDAM-DATA:gene_family
        <family_id> RO:has_member <gene1>
        <family_id> RO:has_member <gene2>

        :param family_id:
        :param g: the graph to modify
        :return:
        """
        family = Family(self.graph)
        gene_family = self.globaltt['gene_family']

        # make the assumption that the genes
        # have already been added as classes previously
        self.model.addIndividualToGraph(family_id, None, gene_family)

        # add each gene to the family
        family.addMember(family_id, self.sub)
        family.addMember(family_id, self.obj)

        return
