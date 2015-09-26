import sys
import traceback
from abc import ABCMeta
from abc import abstractmethod


'''
Abstract base classes for readers.
'''


class TextReader(object):
    '''
    Abstract base class for reading text from files on disk.
    '''

    __metaclass__ = ABCMeta

    def __enter__(self):
        return self

    @abstractmethod
    def read(self, filename):
        pass

    def __exit__(self, except_type, except_value, except_trace):
        pass


class ImageReader(object):

    '''
    Abstract base class for reading images from files on disk.
    '''

    __metaclass__ = ABCMeta

    def __enter__(self):
        return self

    @abstractmethod
    def read(self, filename):
        pass

    @abstractmethod
    def read_subset(self, filename, series=None, plane=None):
        pass

    def __exit__(self, except_type, except_value, except_trace):
        pass


class SlideReader(object):

    '''
    Abstract base class for reading whole slide images from files on disk.
    '''

    __metaclass__ = ABCMeta

    def __enter__(self):
        return self

    @abstractmethod
    def read(self, filename):
        pass

    @abstractmethod
    def split(self):
        pass

    def __exit__(self, except_type, except_value, except_trace):
        pass


class MetadataReader(object):

    '''
    Abstract base class for reading metadata from additional (non-image) files.

    They return metadata as OMEXML objects, according to the OME data model,
    see `python-bioformats <http://pythonhosted.org/python-bioformats/#metadata>`_.

    Unfortunately, the documentation is very sparse.
    If you need additional information, refer to the relevant
    `source code <https://github.com/CellProfiler/python-bioformats/blob/master/bioformats/omexml.py>`_.

    Note
    ----
    In case custom readers provide a *Plate* element, they also have to specify
    an *ImageRef* elements for each *WellSample* element, which serve as
    references to OME *Image* elements. Each *ImageRef* attribute must be a
    dictionary with a single entry. The value must be a list of strings, where
    each element represents the reference information that can be used to map
    the *WellSample* element to an individual *Image* element. The key must be
    a regular expression string that can be used to extract the reference
    information from the corresponding image filenames.
    '''

    __metaclass__ = ABCMeta

    def __enter__(self):
        return self

    @abstractmethod
    def read(self, filename):
        pass

    def __exit__(self, except_type, except_value, except_trace):
        if except_value:
            sys.stdout.write('The following error occurred:\n%s'
                             % str(except_value))
            for tb in traceback.format_tb(except_trace):
                sys.stdout.write(tb)
