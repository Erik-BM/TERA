"""
A set of classes for aligning data aggregated with tools in DataAggregation.
"""
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, OWL, RDFS
import pandas as pd
import validators
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from collections import defaultdict
import tera.utils as ut
import copy
from tqdm import tqdm

class Alignment:
    def __init__(self, verbose = False, name = 'Alignment'):
        """Base class for alignment of two data sets. 
        
        Parameters
        ----------
        name : str        
        """
        self.name = name
        self.verbose = verbose
    
    def __add__(self, other):
        c = copy.deepcopy(self)
        if hasattr(c, 'mappings'):
            c.mappings.update(other.mappings)
        if hasattr(c, 'reverse_mappings'):
            c.reverse_mappings.update(other.reverse_mappings)
        return c
    
    def load(self):
        """Loading mappings. 
        
        Raises
        ------
        NotImplementedError
            * If not implemented in sub-class.
        """
        raise NotImplementedError
    
    def _to_defaultdict(self):
        self.mappings = defaultdict(lambda :'no mapping', self.mappings)
    
    def _mapping(self, x, reverse = False):
        """
        Maps from one id type to another. 
        
        Parameters
        ----------
        x : rdflib.URIRef or str 
            URI/identifier to map from. 
            
        reverse : bool 
            Reverse the direction of mapping. 
                
        Returns
        -------
        str 
            If no mapping exists, returns 'no mapping'
        """
        if not hasattr(self, 'mappings'):
            self.load()
        if not hasattr(self, 'reverse_mappings'):
            self.reverse_mappings = {}
            for k,i in self.mappings.items():
                for j in i:
                    self.reverse_mappings[j] = [k]
            
        if reverse:
            tmp = self.reverse_mappings
        else:
            tmp = self.mappings
        
        x = str(x)
        if x in tmp:
            if len(tmp) > 1 and self.verbose:
                print('Mapping from %s is not unique.' % x)
            try:
                return tmp[x].pop(0)
            except:
                return 'no mapping'
            
        return 'no mapping'
    
    def __len__(self):
        return len(self.mappings)
    
    def __add__(self,other):
        self.load()
        other.load()
        self.mappings = {**self.mappings,**other.mappings}
        return self
        
    @ut.do_recursively_in_class
    def convert(self, id_, reverse=False, strip=False):
        """
        Convert a set of ids into new identifiers.
        
        Parameters
        ----------
        id_ : rdflib.URIRef, str, list, set 
            URI(s)/identifier(s)  
        
        reverse : bool 
            Reverse the direction of mapping. 
            
        strip : bool 
            Remove namespace.
                
        Returns
        -------
        str or dict
            Mapped values.
        """
        if strip:
            id_ = ut.strip_namespace(str(id_),['/','#'])
        return self._mapping(id_,reverse)

class EndpointMapping(Alignment):
    def __init__(self, endpoint, verbose=False):
        super(EndpointMapping, self).__init__(verbose=verbose)
        """Class for loading mappings based on owl:sameAs property.
        
        Parameters
        ----------
        endpoint : str 
            SPARQL endpoint URL.
        """
        self.endpoint = endpoint
    
    def load(self):
        query = """
        SELECT ?s ?o WHERE {
            ?s <http://www.w3.org/2002/07/owl#sameAs> ?o .
        } 
        """
        res = ut.query_endpoint(self.endpoint, query, var = ['s','o'])
        self.mappings = {str(s):[str(o)] for s,o in res}

class WikidataMapping(Alignment):
    def __init__(self, query, verbose=False):
        """
        Class for loading mappings from wikidata.
        
        Parameters
        ----------
        query : str 
            Wikidata query with two variables. 
            
            eg. from inchikey to cas: 
            
            SELECT ?from ?to {  
            ?compound wdt:P235 ?from . 
            ?compound wdt:P231 ?to .} 
        """
        super(WikidataMapping, self).__init__(verbose=verbose)
        self.query = query
        
    def load(self):
        res = ut.query_endpoint('https://query.wikidata.org/sparql', 
                             self.query, 
                             var = ['from', 'to'])
        self.mappings = {str(f):[str(t)] for f,t in res}

class LogMapMapping(Alignment):
    def __init__(self, filename, threshold=0.95, unique=False,  verbose=False, strip=True):
        """
        Class for using LogMap (or other system) alignments. 
        
        Parameters
        ----------
        filename : str 
            Path to logmap output file (.rdf) 
        
        threshold : float 
            Alignment threshold.
        """
        super(LogMapMapping, self).__init__(verbose=verbose)
        
        self.threshold = threshold
        self.filename = filename
        self.strip = strip
        self.unique = unique
        
    def load(self):
        if self.filename[-3:] == 'rdf':
            self.load_rdf()
        else:
            self.load_txt()
    
    def load_rdf(self):
        out = defaultdict(list)
        scores = defaultdict(lambda : 0.0)
        g = Graph()
        g.parse(self.filename)
        o = URIRef('http://knowledgeweb.semanticweb.org/heterogeneity/alignmentCell')
        for s in g.subjects(predicate=RDF.type, object = o):
            e1 = list(g.objects(subject=s,predicate=URIRef('http://knowledgeweb.semanticweb.org/heterogeneity/alignmententity1'))).pop(0)
            e2 = list(g.objects(subject=s,predicate=URIRef('http://knowledgeweb.semanticweb.org/heterogeneity/alignmententity2'))).pop(0)
            score = list(g.objects(subject=s,predicate=URIRef('http://knowledgeweb.semanticweb.org/heterogeneity/alignmentmeasure'))).pop(0)
            
            score = float(score)
            if score >= self.threshold and (score > scores[(e1,e2)] or not self.unique):
                scores[(e1,e2)] = score
                e1 = str(e1)
                e2 = str(e2)
                if self.strip:
                    e1 = ut.strip_namespace(e1,['/','#','CID'])
                    e2 = ut.strip_namespace(e2,['/','#','CID'])
                out[e1].append(e2)
        
        self.mappings = out
        self.scores = scores
        
    def load_txt(self):
        out = defaultdict(list)
        scores = defaultdict(lambda : 0.0)
        try:
            df = pd.read_csv(self.filename, sep='|', header=0, names=['e1','e2','type','score','is_instance'])
        except:
            df = pd.read_csv(self.filename, sep='|', header=0, names=['e1','e2','score'])
            
        for e1,e2,score in zip(df['e1'],df['e2'],df['score']):
            score = float(score)
            if score >= self.threshold and (score > scores[(e1,e2)] or not self.unique):
                scores[(e1,e2)] = score
                e1 = str(e1)
                e2 = str(e2)
                if self.strip:
                    e1 = ut.strip_namespace(e1,['/','#','CID'])
                    e2 = ut.strip_namespace(e2,['/','#','CID'])
                out[e1].append(e2)
        self.mappings = out
        self.scores = scores
        
class StringMatchingMapping(Alignment):
    def __init__(self, dict1, dict2, threshold = 0.95, verbose=False):
        """
        Class for creating mapping between two label dictonaries using string matching. 
        
        Parameters
        ----------
        dict1 : dict 
            Dictonary on the form {entity:list of labels} 
        
        dict2 : dict 
            Same as dict1.
            
        threshold : float 
            Alignment threshold.
        """
        super(StringMatchingMapping, self).__init__(verbose=verbose)
        
        self.threshold = threshold
        self.dict1 = dict1
        self.dict2 = dict2
    
    def load(self):
        tmp = defaultdict(float)
        for k1 in self.dict1:
            for k2 in self.dict2:
                try:
                    _, score = process.extractOne(self.dict1[k1],self.dict2[k2])
                except TypeError:
                    score = 0
                    
                if score >= self.threshold:
                    tmp[k1,k2] = max(tmp[k1,k2],score)
        
        self.mappings = {k1:[k2] for k1,k2 in tmp}
    
class DownloadedWikidata(Alignment):
    def __init__(self, filename, verbose = False):
        """
        Class for creating mappings from downloaded wikidata files. 
        
        Parameters
        ----------
        filename : str 
            Path to file with header = ['from','to']
            
        """
        super(DownloadedWikidata, self).__init__(verbose=verbose)
        self.filename = filename
    
    def load(self):
        df = pd.read_csv(self.filename,dtype=str)
        self.mappings = {k1:[k2] for k1,k2 in zip(df['from'],df['to'])}
    
class StringGraphMapping(Alignment):
    def __init__(self, g1, g2, threshold = 0.95, verbose=False):
        """
        Class for creating mapping between two graph using string matching. 
        
        Parameters
        ----------
        g1 : rdflib.Graph 
        
        g2 : rdflib.Graph 
        
        threshold : float 
            Alignment threshold.
                
        """
        super(StringGraphMapping, self).__init__(verbose=verbose)
        
        self.threshold = threshold
        self.g1 = g1
        self.g2 = g2
    
    def load(self):
        dict1 = ut.graph_to_dict(self.g1)
        dict2 = ut.graph_to_dict(self.g2)
        
        tmp = defaultdict(float)
        for k1 in dict1:
            for k2 in dict2:
                try:
                    _, score = process.extractOne(dict1[k1],dict2[k2])
                except TypeError:
                    score = 0
                    
                if score >= self.threshold:
                    tmp[k1,k2] = max(tmp[k1,k2],score)
        
        self.mappings = {k1:[k2] for k1,k2 in tmp}

class InchikeyToCas(WikidataMapping):
    def __init__(self, verbose=False):
        """Class which creates inchikey to cas mapping."""
        query = """
        SELECT ?from ?to WHERE
            { 
            [] wdt:P31 wd:Q11173 ;
               wdt:P235 ?from ;
               wdt:P231 ?tmp .
              BIND(REPLACE(?tmp, "-", "", "i") AS ?to)
            }
        """
        super(InchikeyToCas, self).__init__(query=query, verbose=verbose)
    
class InchikeyToPubChem(WikidataMapping):
    def __init__(self, verbose=False):
        """Class which creates inchikey to pubchem mapping."""
        query = """
        SELECT ?from ?to WHERE
            { 
            [] wdt:P31 wd:Q11173 ;
               wdt:P235 ?from ;
               wdt:P662 ?to .
            }
        """
        super(InchikeyToPubChem, self).__init__(query=query, verbose=verbose)
    
class InchikeyToChEBI(WikidataMapping):
    def __init__(self, verbose=False):
        """Class which creates inchikey to chebi mapping."""
        query = """
        SELECT ?from ?to WHERE
            { 
            [] wdt:P31 wd:Q11173 ;
               wdt:P235 ?from ;
               wdt:P683 ?to .
            }
        """
        super(InchikeyToChEBI, self).__init__(query=query, verbose=verbose)

class InchikeyToChEMBL(WikidataMapping):
    def __init__(self, verbose=False):
        """Class which creates inchikey to chemble mapping."""
        query = """
        SELECT ?from ?to WHERE
            { 
            [] wdt:P31 wd:Q11173 ;
               wdt:P235 ?from ;
               wdt:P592 ?to .
            }
        """
        super(InchikeyToChEMBL, self).__init__(query=query, verbose=verbose)
        
class InchikeyToMeSH(WikidataMapping):
    def __init__(self, verbose=False):
        """Class which creates inchikey to mesh mapping."""
        query = """
        SELECT ?from ?to WHERE
            { 
            [] wdt:P31 wd:Q11173 ; 
               wdt:P235 ?from ;
               wdt:P486 ?to .
            }
        """
        super(InchikeyToMeSH, self).__init__(query=query, verbose=verbose)

class NCBIToEOL(WikidataMapping):
    def __init__(self, verbose=False):
        """Class which creates ncbi to eol mapping."""
        query = """
        SELECT ?from ?to WHERE
            { 
            [] wdt:P31 wd:Q16521 ; 
               wdt:P685 ?from ;
               wdt:P830 ?to .
            }
        """
        super(NCBIToEOL, self).__init__(query=query, verbose=verbose)
        
        
#TODO change ncbi -> ecotox mapping to concensus mappings.
class NCBIToEcotox(StringGraphMapping):
    def __init__(self, dataobject1, dataobject2, verbose=False):
        """Class which creates ncbi to ecotox mapping."""
        super(NCBIToEcotox, self).__init__(dataobject1.graph,
                                           dataobject2.graph, 
                                           verbose=verbose)
        





